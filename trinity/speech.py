from __future__ import annotations

import io
import os
import re
from typing import Any, Iterator

import numpy as np
import requests

DEFAULT_API_ROOT = "https://api.elevenlabs.io"
# Residency keys are rejected on the global host with a 401 that looks like
# "the key is invalid" unless we retry the matching isolated API.
API_ROOTS = (
    DEFAULT_API_ROOT,
    "https://api.us.elevenlabs.io",
    "https://api.eu.residency.elevenlabs.io",
    "https://api.in.residency.elevenlabs.io",
    "https://api.sg.residency.elevenlabs.io",
)
API_ROOT = DEFAULT_API_ROOT
# 22.05 kHz PCM needs no decoding, so playback can start as the audio arrives.
STREAM_FORMAT = "pcm_22050"
STREAM_RATE = 22050
MODEL_FALLBACKS = (
    "eleven_turbo_v2_5",
    "eleven_flash_v2_5",
    "eleven_multilingual_v2",
)

_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\xa0"), None)
_SK_KEY_RE = re.compile(r"sk_[A-Za-z0-9]+")
_HEADER_KEY_RE = re.compile(r"(?i)(?:xi-api-key|api[_-]?key)\s*[:=]\s*([A-Za-z0-9_\-]+)")
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,28}$")
_PREFIXES = (
    "xi-api-key:",
    "xi-api-key=",
    "api-key:",
    "api_key:",
    "api key:",
    "bearer ",
    "elevenlabs_api_key=",
    "elevenlabs_api_key:",
)


class SpeechError(RuntimeError):
    pass


def normalize_api_key(raw: str) -> str:
    """Accept a dashboard copy, curl snippet, or quoted export and return the key."""
    text = (raw or "").translate(_INVISIBLE).strip()
    if not text:
        return ""
    found = _SK_KEY_RE.search(text)
    if found:
        return found.group(0)
    found = _HEADER_KEY_RE.search(text)
    if found:
        return found.group(1)
    lowered = text.lower()
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip()
            lowered = text.lower()
            break
    text = text.strip().strip("\"'").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text.split()[0] if text else ""


def describe_key(key: str) -> str:
    key = normalize_api_key(key)
    if not key:
        return "no key"
    if len(key) <= 8:
        return f"{key[:2]}… ({len(key)} chars)"
    return f"{key[:4]}…{key[-4:]} ({len(key)} chars)"


def key_problem(key: str) -> str | None:
    key = normalize_api_key(key)
    if not key:
        return "No ElevenLabs API key is set."
    if key.lower().startswith("http"):
        return "That looks like a URL, not an API key. Copy the key from elevenlabs.io → Developers → API Keys."
    if not key.startswith("sk_") and _VOICE_ID_RE.match(key):
        return (
            "That looks like a Voice ID, not an API key. "
            "Copy the key from elevenlabs.io → Developers → API Keys."
        )
    if len(key) < 20:
        return "That key is too short. Paste the full key from elevenlabs.io → Developers → API Keys."
    return None


def explain_response(response: requests.Response) -> str:
    message, status = _detail(response)
    lowered = (message + " " + status).lower()
    if response.status_code == 401:
        if any(word in lowered for word in ("residency", "isolated", "global server")):
            return message or "This API key belongs to a different ElevenLabs region."
        return (
            message
            or "ElevenLabs rejected the API key. Copy a new key from elevenlabs.io → Developers → API Keys."
        )
    if response.status_code == 403:
        return (
            message
            or "This API key is missing permission for that action. "
            "Enable Text to Speech, and Voices if you want to list them."
        )
    if response.status_code == 429:
        return message or "ElevenLabs is rate limiting or out of credits."
    return f"ElevenLabs error {response.status_code}: {message or response.text[:200]}"


def _detail(response: requests.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return ((response.text or "")[:240], "")
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        return (
            str(detail.get("message") or detail.get("status") or ""),
            str(detail.get("status") or ""),
        )
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item.get("message") or item))
            else:
                parts.append(str(item))
        return ("; ".join(part for part in parts if part), "")
    if detail:
        return (str(detail), "")
    return ((response.text or "")[:240], "")


def _needs_host_hunt(message: str) -> bool:
    lowered = message.lower()
    return any(
        word in lowered
        for word in ("residency", "isolated", "global server", "wrong environment")
    )


class ElevenLabsVoice:
    """Cloud speech for Trinity. Only used when an API key is present."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "eleven_turbo_v2_5",
        stability: float = 0.45,
        similarity: float = 0.8,
        api_root: str = "",
    ) -> None:
        self.api_key = normalize_api_key(api_key)
        self.voice_id = voice_id.strip()
        self.model = model
        self.stability = stability
        self.similarity = similarity
        configured = (api_root or os.environ.get("ELEVENLABS_BASE_URL") or "").strip()
        self.api_root = configured.rstrip("/") or DEFAULT_API_ROOT
        self.verified = False
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.voice_id)

    def list_voices(self) -> list[tuple[str, str]]:
        data = self._get_json("/v1/voices", params={"show_legacy": "true"})
        voices = []
        for item in data.get("voices") or []:
            name = str(item.get("name") or "").strip()
            voice_id = str(item.get("voice_id") or "").strip()
            if name and voice_id:
                voices.append((name, voice_id))
        return sorted(voices)

    def check(self) -> str:
        problem = key_problem(self.api_key)
        if problem:
            self.verified = False
            self.last_error = problem
            raise SpeechError(problem)
        try:
            data = self._get_json("/v1/user")
        except SpeechError as exc:
            # Some restricted keys can speak but cannot read the user profile.
            if "403" not in str(exc) and "permission" not in str(exc).lower():
                self.verified = False
                self.last_error = str(exc)
                raise
            self._get_json("/v1/models")
            data = {}
        self.verified = True
        self.last_error = ""
        sub = data.get("subscription") if isinstance(data, dict) else {}
        if not isinstance(sub, dict):
            sub = {}
        tier = str(sub.get("tier") or sub.get("status") or "account")
        used, limit = sub.get("character_count"), sub.get("character_limit")
        quota = ""
        if isinstance(used, int) and isinstance(limit, int):
            quota = f"{used:,}/{limit:,} chars"
        voice_note = ""
        try:
            voices = self.list_voices()
            match = next((name for name, vid in voices if vid == self.voice_id), None)
            if match:
                voice_note = f" Voice: {match}."
            elif voices:
                voice_note = (
                    f" Saved voice id is not in this account ({len(voices)} available)."
                )
        except SpeechError:
            voice_note = " Text-to-speech is available; listing voices needs the Voices permission."
        extra = f" ({quota})" if quota else ""
        return f"ElevenLabs ready · {tier}{extra}.{voice_note}"

    def stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield float32 mono blocks at STREAM_RATE as they arrive."""
        if not self.api_key:
            raise SpeechError("No ElevenLabs API key is set.")
        if not self.voice_id:
            raise SpeechError("No ElevenLabs voice is selected.")
        problem = key_problem(self.api_key)
        if problem:
            raise SpeechError(problem)
        spoken = text.strip()
        if not spoken:
            raise SpeechError("Nothing to speak.")

        models: list[str] = []
        for model in (self.model, *MODEL_FALLBACKS):
            if model and model not in models:
                models.append(model)
        last_error: SpeechError | None = None
        for model in models:
            for output_format, kind in ((STREAM_FORMAT, "pcm"), ("mp3_44100_128", "mp3")):
                try:
                    yield from self._stream_once(spoken, model, output_format, kind)
                    if model != self.model:
                        self.model = model
                    return
                except SpeechError as exc:
                    last_error = exc
                    message = str(exc).lower()
                    if "invalid api key" in message or "rejected the api key" in message:
                        if not _needs_host_hunt(str(exc)):
                            raise
                    if kind == "pcm" and any(
                        word in message for word in ("format", "output", "pcm", "accept")
                    ):
                        continue
                    if any(word in message for word in ("model", "does not exist", "invalid_model")):
                        break
                    if kind == "mp3":
                        continue
                    raise
        raise last_error or SpeechError("ElevenLabs could not speak that.")

    def _stream_once(
        self, text: str, model: str, output_format: str, kind: str
    ) -> Iterator[np.ndarray]:
        payload: dict[str, Any] = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity,
            },
        }
        last_error: SpeechError | None = None
        for root in self._roots():
            try:
                with requests.post(
                    f"{root}/v1/text-to-speech/{self.voice_id}/stream",
                    params={"output_format": output_format},
                    json=payload,
                    headers={"xi-api-key": self.api_key},
                    stream=True,
                    timeout=(15, 180),
                ) as response:
                    if response.status_code >= 400:
                        message = explain_response(response)
                        if response.status_code == 401 and _needs_host_hunt(message):
                            last_error = SpeechError(message)
                            continue
                        raise SpeechError(message)
                    self.api_root = root
                    if kind == "mp3":
                        audio = decode_mp3(response.content, 44100)
                        if audio.size:
                            yield audio
                        return
                    yield from _iter_pcm(response)
                    return
            except requests.RequestException as exc:
                raise SpeechError(f"ElevenLabs request failed: {exc}") from exc
        raise last_error or SpeechError("Could not reach ElevenLabs.")

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise SpeechError("No ElevenLabs API key is set.")
        last_error: SpeechError | None = None
        for root in self._roots():
            try:
                response = requests.get(
                    f"{root}{path}",
                    headers={"xi-api-key": self.api_key},
                    params=params,
                    timeout=20,
                )
            except requests.RequestException as exc:
                raise SpeechError(f"Could not reach ElevenLabs: {exc}") from exc
            if response.status_code >= 400:
                message = explain_response(response)
                if response.status_code == 401:
                    last_error = SpeechError(message)
                    continue
                raise SpeechError(message)
            try:
                data = response.json()
            except ValueError as exc:
                raise SpeechError(f"ElevenLabs sent unreadable JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise SpeechError("ElevenLabs sent an unexpected response.")
            self.api_root = root
            return data
        self.verified = False
        self.last_error = str(last_error) if last_error else "Could not reach ElevenLabs."
        raise last_error or SpeechError("Could not reach ElevenLabs.")

    def _roots(self) -> list[str]:
        roots = [self.api_root]
        for root in API_ROOTS:
            if root not in roots:
                roots.append(root)
        return roots


def _iter_pcm(response: requests.Response) -> Iterator[np.ndarray]:
    tail = b""
    for chunk in response.iter_content(chunk_size=4096):
        if not chunk:
            continue
        data = tail + chunk
        usable = len(data) - (len(data) % 2)
        tail = data[usable:]
        if usable:
            block = np.frombuffer(data[:usable], dtype="<i2")
            yield block.astype(np.float32) / 32768.0


def decode_mp3(data: bytes, target_rate: int) -> np.ndarray:
    """Fallback decoder, used only if a non-PCM format comes back."""
    import av

    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)
        blocks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                blocks.append(resampled.to_ndarray().reshape(-1))
    if not blocks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(blocks).astype(np.float32)
