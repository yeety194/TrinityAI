from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from piper import PiperVoice
from piper.config import SynthesisConfig

from trinity.config import DATA_DIR, load_config

Listener = Callable[[str], None]
VOICE_DIR = DATA_DIR / "voices"
VOICE_CATALOG = {
    "Amy (English, US)": "en_US-amy-medium",
    "Lessac (English, US)": "en_US-lessac-medium",
    "Ryan (English, US)": "en_US-ryan-medium",
}


class Voice:
    def __init__(self) -> None:
        cfg = load_config()["voice"]
        self.wake_word = str(cfg["wake_word"]).lower().strip()
        self.sample_rate = int(cfg["sample_rate"])
        self.silence_seconds = float(cfg["silence_seconds"])
        self.max_record_seconds = float(cfg["max_record_seconds"])
        self.speak_replies = bool(cfg["speak_replies"])
        self._piper_length_scale = float(cfg.get("piper_length_scale", 0.95))
        self._voice_key = str(cfg.get("piper_voice", "en_US-amy-medium"))
        self._whisper_name = str(cfg["whisper_model"])
        self._whisper: WhisperModel | None = None
        self._piper: PiperVoice | None = None
        self._tts_lock = threading.Lock()
        self._stop_speak = threading.Event()
        self._listening = threading.Event()
        self._armed = threading.Event()
        self._busy = threading.Event()
        self._on_transcript: Listener | None = None
        self._on_status: Listener | None = None
        self._listen_thread: threading.Thread | None = None

    def set_handlers(self, on_transcript: Listener, on_status: Listener) -> None:
        self._on_transcript = on_transcript
        self._on_status = on_status

    def voice_names(self) -> list[str]:
        return list(VOICE_CATALOG)

    def current_voice_name(self) -> str:
        for display, key in VOICE_CATALOG.items():
            if key == self._voice_key:
                return display
        return next(iter(VOICE_CATALOG))

    def current_voice_key(self) -> str:
        return self._voice_key

    def set_voice(self, display_name: str) -> None:
        key = VOICE_CATALOG.get(display_name)
        if key:
            self.interrupt_speech()
            self._voice_key = key
            self._piper = None

    def load_stt(self) -> None:
        if self._whisper is None:
            self._status("Loading speech model…")
            self._whisper = WhisperModel(self._whisper_name, device="cpu", compute_type="int8")
            self._status("Speech ready")

    def arm(self, enabled: bool) -> None:
        if enabled:
            self._armed.set()
            self.start_listening()
        else:
            self._armed.clear()
            self.stop_listening()

    def is_armed(self) -> bool:
        return self._armed.is_set()

    def start_listening(self) -> None:
        if self._listening.is_set():
            return
        self._listening.set()
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()
        self._status("Listening")

    def stop_listening(self) -> None:
        self._listening.clear()
        self._status("Standby")

    def listen_once(self) -> None:
        threading.Thread(target=self._capture_utterance, args=(True,), daemon=True).start()

    def speak(self, text: str) -> None:
        if not self.speak_replies or not text.strip():
            return
        self._stop_speak.clear()
        threading.Thread(target=self._speak_sync, args=(text,), daemon=True).start()

    def interrupt_speech(self) -> None:
        self._stop_speak.set()
        try:
            sd.stop()
        except Exception:
            pass

    def _speak_sync(self, text: str) -> None:
        with self._tts_lock:
            if self._stop_speak.is_set():
                return
            try:
                self._status("Speaking locally")
                voice = self._load_piper_voice()
                config = SynthesisConfig(length_scale=self._piper_length_scale)
                for chunk in voice.synthesize(text, syn_config=config):
                    if self._stop_speak.is_set():
                        break
                    sd.play(chunk.audio_float_array, chunk.sample_rate)
                    sd.wait()
            except Exception as exc:
                self._status(f"Local voice unavailable: {exc}")
                return
            if self._listening.is_set():
                self._status("Listening")
            else:
                self._status("Standby")

    def _load_piper_voice(self) -> PiperVoice:
        if self._piper is not None:
            return self._piper
        model = VOICE_DIR / f"{self._voice_key}.onnx"
        config = VOICE_DIR / f"{self._voice_key}.onnx.json"
        if not model.exists() or not config.exists():
            raise RuntimeError(
                f"{self.current_voice_name()} is not installed. Run setup_local_voice.ps1 once."
            )
        self._piper = PiperVoice.load(model, config_path=config)
        return self._piper

    def _listen_loop(self) -> None:
        try:
            self.load_stt()
        except Exception as exc:
            self._status(f"Speech model failed: {exc}")
            self._listening.clear()
            return
        while self._listening.is_set():
            self._capture_utterance(force=False)

    def _capture_utterance(self, force: bool) -> None:
        if self._busy.is_set() and not force:
            return
        self._busy.set()
        try:
            self.load_stt()
            if force:
                self._status("Listening…")
            audio = self._record_until_silence(force=force)
            if audio is None or audio.size < self.sample_rate * 0.25:
                if force:
                    self._status("Didn't catch that")
                return
            text = self._transcribe(audio)
            if not text:
                if force:
                    self._status("Didn't catch that")
                return
            if not force and self._armed.is_set():
                if not _addressed_to_trinity(text, self.wake_word):
                    return
                text = _strip_wake(text, self.wake_word)
                if not text:
                    self._status("Yes?")
                    return
            if self._on_transcript:
                self._on_transcript(text)
        except Exception as exc:
            self._status(f"Mic error: {exc}")
        finally:
            self._busy.clear()

    def _record_until_silence(self, force: bool) -> np.ndarray | None:
        q: queue.Queue[np.ndarray] = queue.Queue()
        silence_needed = int(self.silence_seconds * self.sample_rate)
        max_frames = int(self.max_record_seconds * self.sample_rate)
        started = False
        silent = 0
        chunks: list[np.ndarray] = []
        total = 0
        rms_floor = 0.012
        idle_timeout = 8.0 if force else 2.5
        idle = 0.0

        def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
            q.put(indata.copy())

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            ):
                while total < max_frames:
                    if not force and not self._listening.is_set():
                        break
                    try:
                        data = q.get(timeout=0.2)
                    except queue.Empty:
                        idle += 0.2
                        if started:
                            silent += int(0.2 * self.sample_rate)
                            if silent >= silence_needed:
                                break
                        elif idle >= idle_timeout:
                            break
                        continue
                    idle = 0.0
                    mono = data.reshape(-1)
                    total += mono.size
                    level = float(np.sqrt(np.mean(np.square(mono))))
                    if level >= rms_floor:
                        started = True
                        silent = 0
                        chunks.append(mono)
                    elif started:
                        silent += mono.size
                        chunks.append(mono)
                        if silent >= silence_needed:
                            break
        except Exception as exc:
            self._status(f"Microphone unavailable: {exc}")
            return None

        if not chunks:
            return None
        return np.concatenate(chunks)

    def _transcribe(self, audio: np.ndarray) -> str:
        assert self._whisper is not None
        self._status("Transcribing")
        segments, _info = self._whisper.transcribe(
            audio.astype(np.float32),
            language="en",
            vad_filter=True,
            beam_size=1,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _status(self, message: str) -> None:
        if self._on_status:
            self._on_status(message)


def _addressed_to_trinity(text: str, wake: str) -> bool:
    lowered = text.lower().strip()
    return lowered.startswith(wake) or f" {wake}" in lowered[:40] or lowered.rstrip(" ,.?!") == wake


def _strip_wake(text: str, wake: str) -> str:
    lowered = text.lower()
    idx = lowered.find(wake)
    if idx == -1:
        return text.strip()
    rest = text[idx + len(wake) :].lstrip(" ,.?!:;-")
    return rest.strip()
