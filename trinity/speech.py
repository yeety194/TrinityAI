from __future__ import annotations

import io
from typing import Any, Iterator

import numpy as np
import requests

API_ROOT = "https://api.elevenlabs.io/v1"
# 22.05 kHz PCM needs no decoding, so playback can start as the audio arrives.
STREAM_FORMAT = "pcm_22050"
STREAM_RATE = 22050


class SpeechError(RuntimeError):
    pass


class ElevenLabsVoice:
    """Cloud speech for Trinity. Only used when an API key is present."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "eleven_turbo_v2_5",
        stability: float = 0.45,
        similarity: float = 0.8,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.stability = stability
        self.similarity = similarity

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.voice_id)

    def list_voices(self) -> list[tuple[str, str]]:
        if not self.api_key:
            raise SpeechError("No ElevenLabs API key is set.")
        try:
            response = requests.get(
                f"{API_ROOT}/voices", headers={"xi-api-key": self.api_key}, timeout=20
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise SpeechError(f"Could not reach ElevenLabs: {exc}") from exc
        except ValueError as exc:
            raise SpeechError(f"ElevenLabs sent unreadable JSON: {exc}") from exc
        voices = []
        for item in data.get("voices") or []:
            name = str(item.get("name") or "").strip()
            voice_id = str(item.get("voice_id") or "").strip()
            if name and voice_id:
                voices.append((name, voice_id))
        return sorted(voices)

    def check(self) -> str:
        voices = self.list_voices()
        match = next((name for name, vid in voices if vid == self.voice_id), None)
        if match:
            return f"ElevenLabs ready with {match}."
        return f"ElevenLabs ready, but the saved voice id is not in your account ({len(voices)} available)."

    def stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield float32 mono blocks at STREAM_RATE as they arrive."""
        if not self.configured:
            raise SpeechError("ElevenLabs is not configured.")
        payload: dict[str, Any] = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity,
            },
        }
        try:
            with requests.post(
                f"{API_ROOT}/text-to-speech/{self.voice_id}/stream",
                params={"output_format": STREAM_FORMAT},
                json=payload,
                headers={"xi-api-key": self.api_key, "Accept": "audio/pcm"},
                stream=True,
                timeout=(15, 180),
            ) as response:
                if response.status_code >= 400:
                    raise SpeechError(_explain(response))
                tail = b""
                for chunk in response.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    data = tail + chunk
                    # Keep any trailing odd byte for the next 16-bit sample.
                    usable = len(data) - (len(data) % 2)
                    tail = data[usable:]
                    if usable:
                        block = np.frombuffer(data[:usable], dtype="<i2")
                        yield block.astype(np.float32) / 32768.0
        except requests.RequestException as exc:
            raise SpeechError(f"ElevenLabs request failed: {exc}") from exc


def _explain(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("status") or ""
    else:
        message = detail or ""
    if response.status_code == 401:
        return "ElevenLabs rejected the API key."
    if response.status_code == 429:
        return "ElevenLabs is rate limiting or out of credits."
    return f"ElevenLabs error {response.status_code}: {message or response.text[:200]}"


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
