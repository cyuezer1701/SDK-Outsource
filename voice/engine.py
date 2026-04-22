"""Voice engine — offline STT (faster-whisper) + TTS (pyttsx3/espeak-ng)."""

from __future__ import annotations

import subprocess
import threading
from typing import Callable

import numpy as np

_SAMPLE_RATE = 16_000
_CHUNK_MS = 50                  # chunk size in milliseconds
_CHUNK_SAMPLES = int(_SAMPLE_RATE * _CHUNK_MS / 1000)
_RMS_THRESHOLD = 0.015          # amplitude threshold for speech detection
_SILENCE_TIMEOUT_S = 1.2        # seconds of silence before sending to Whisper
_MIN_SPEECH_S = 0.4             # ignore clips shorter than this
_SILENCE_CHUNKS = int(_SILENCE_TIMEOUT_S * 1000 / _CHUNK_MS)
_MIN_SPEECH_CHUNKS = int(_MIN_SPEECH_S * 1000 / _CHUNK_MS)


class VoiceEngine:
    """Always-on offline voice interface.

    Listens continuously via sounddevice, detects speech with energy-based
    VAD, transcribes with faster-whisper, and reads responses aloud with
    pyttsx3 (espeak-ng backend on Linux).

    Args:
        on_transcript: Called on the main logic thread with the recognised text.
        language: Whisper language code (default "de").
    """

    def __init__(self, on_transcript: Callable[[str], None], language: str = "de") -> None:
        self._on_transcript = on_transcript
        self._language = language
        self._running = False
        self._listening = False
        self._thread: threading.Thread | None = None
        self._speak_lock = threading.Lock()
        self._model = None          # loaded lazily
        self._tts = None            # loaded lazily
        self._init_error: str | None = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the always-on listening loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._listening = False

    def is_listening(self) -> bool:
        return self._listening

    def get_init_error(self) -> str | None:
        return self._init_error

    def speak(self, text: str) -> None:
        """Speak text via TTS (blocks until finished)."""
        if not text.strip():
            return
        with self._speak_lock:
            try:
                tts = self._get_tts()
                if tts is None:
                    # fallback: espeak-ng subprocess
                    subprocess.run(
                        ["espeak-ng", "-v", self._language, "-s", "160", text],
                        capture_output=True, timeout=30,
                    )
                    return
                tts.say(text)
                tts.runAndWait()
            except Exception:
                try:
                    subprocess.run(
                        ["espeak-ng", "-v", self._language, "-s", "160", text],
                        capture_output=True, timeout=30,
                    )
                except Exception:
                    pass

    # ── Internal helpers ─────────────────────────────────────────────────

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel("small", device="cpu", compute_type="int8")
            except Exception as exc:
                self._init_error = f"Whisper konnte nicht geladen werden: {exc}"
                self._model = False
        return self._model if self._model is not False else None

    def _get_tts(self):
        if self._tts is None:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                # Try to find a German voice
                voices = engine.getProperty("voices")
                for v in voices:
                    if "de" in (v.languages[0] if v.languages else b"").decode("utf-8", errors="ignore").lower() \
                       or "german" in v.name.lower() \
                       or "deutsch" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
                engine.setProperty("rate", 160)
                engine.setProperty("volume", 1.0)
                self._tts = engine
            except Exception:
                self._tts = False
        return self._tts if self._tts is not False else None

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self._get_model()
        if model is None:
            return ""
        try:
            audio_f32 = audio.astype(np.float32)
            segments, _ = model.transcribe(
                audio_f32,
                language=self._language,
                beam_size=5,
                vad_filter=True,
            )
            return " ".join(s.text for s in segments).strip()
        except Exception:
            return ""

    def _listen_loop(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self._init_error = f"sounddevice nicht verfügbar: {exc}"
            return

        # Warm up the model in background
        threading.Thread(target=self._get_model, daemon=True).start()
        threading.Thread(target=self._get_tts, daemon=True).start()

        try:
            stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=_CHUNK_SAMPLES,
            )
        except Exception as exc:
            self._init_error = f"Kein Mikrofon gefunden: {exc}"
            return

        self._listening = True
        buffer: list[np.ndarray] = []
        silence_count = 0
        recording = False

        with stream:
            while self._running:
                try:
                    chunk, _ = stream.read(_CHUNK_SAMPLES)
                    chunk = chunk[:, 0]  # mono
                except Exception:
                    break

                rms = float(np.sqrt(np.mean(chunk ** 2)))

                if not recording:
                    if rms > _RMS_THRESHOLD:
                        recording = True
                        buffer = [chunk]
                        silence_count = 0
                else:
                    buffer.append(chunk)
                    if rms < _RMS_THRESHOLD:
                        silence_count += 1
                        if silence_count >= _SILENCE_CHUNKS:
                            # Speech ended — transcribe if long enough
                            if len(buffer) >= _MIN_SPEECH_CHUNKS:
                                audio = np.concatenate(buffer)
                                threading.Thread(
                                    target=self._transcribe_and_deliver,
                                    args=(audio,),
                                    daemon=True,
                                ).start()
                            recording = False
                            buffer = []
                            silence_count = 0
                    else:
                        silence_count = 0

        self._listening = False

    def _transcribe_and_deliver(self, audio: np.ndarray) -> None:
        text = self._transcribe(audio)
        if text:
            self._on_transcript(text)
