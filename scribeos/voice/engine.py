"""Offline dictation with faster-whisper + sounddevice (both optional dependencies).

Audio is captured on this computer and never leaves it.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("scribeos.voice")
# Windows often lacks symlink permission; the model cache works fine without, so silence the warning.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

SAMPLE_RATE = 16000
BLOCK = 1600  # 100 ms
SILENCE_END = 0.8  # seconds of silence that end an utterance
MAX_UTTERANCE = 20.0
PARTIAL_EVERY = 1.5


def availability() -> dict:
    missing = []
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        missing.append("faster-whisper")
    try:
        import sounddevice as sd

        try:
            has_input = any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())
        except Exception:
            has_input = False
    except Exception:
        missing.append("sounddevice")
        has_input = False
    return {"available": not missing and has_input, "missing": missing, "microphone": has_input}


class VoiceService:
    """One dictation session at a time. Callbacks are invoked from worker threads."""

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self._model = None
        self._model_name: Optional[str] = None
        self._device = "auto"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _load(self, name: str, emit: Callable[[dict], None]):
        with self._lock:
            if self._model is not None and self._model_name == name:
                return self._model
            from faster_whisper import WhisperModel

            emit({"type": "status", "state": "loading", "message": f"Loading '{name}' speech model (first use downloads it)…"})
            self._model = WhisperModel(name, device=self._device, compute_type="int8", download_root=str(self.models_dir))
            self._model_name = name
            return self._model

    def transcribe(self, audio, model_name: str, language: str, prompt: str = "") -> str:
        try:
            return self._transcribe(audio, model_name, language, prompt)
        except RuntimeError as e:
            # A GPU is present but CUDA/cuBLAS/cuDNN libraries are not: fall back to the CPU.
            if self._device != "cpu" and any(k in str(e).lower() for k in ("cuda", "cublas", "cudnn")):
                log.warning("GPU speech recognition unavailable (%s); using CPU", e)
                with self._lock:
                    self._device = "cpu"
                    self._model = None
                return self._transcribe(audio, model_name, language, prompt)
            raise

    def _transcribe(self, audio, model_name: str, language: str, prompt: str = "") -> str:
        model = self._model if self._model_name == model_name else None
        if model is None:
            model = self._load(model_name, lambda e: None)
        segments, _ = model.transcribe(
            audio,
            language=None if language == "auto" else language,
            vad_filter=True,
            beam_size=1,
            initial_prompt=prompt[:800] or None,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def start(self, model_name: str, language: str, prompt: str, emit: Callable[[dict], None]) -> None:
        if self.running:
            raise RuntimeError("Dictation is already running")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(model_name, language, prompt, emit), daemon=True, name="scribeos-voice")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(5)

    def _run(self, model_name: str, language: str, prompt: str, emit: Callable[[dict], None]) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            self._load(model_name, emit)
        except Exception as e:
            emit({"type": "status", "state": "error", "message": f"Voice engine unavailable: {e}"})
            return
        q: "queue.Queue" = queue.Queue()

        def callback(indata, frames, t, status):  # audio thread
            q.put(indata[:, 0].copy())

        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK, callback=callback)
        except Exception as e:
            emit({"type": "status", "state": "error", "message": f"Microphone error: {e}"})
            return
        emit({"type": "status", "state": "listening"})
        buf: list = []
        speaking = False
        silence = 0.0
        last_partial = 0.0
        noise = 0.004
        with stream:
            while not self._stop.is_set():
                try:
                    block = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                rms = float(np.sqrt(np.mean(block**2)))
                emit({"type": "level", "rms": round(min(1.0, rms * 12), 3)})
                threshold = max(0.01, noise * 3)
                if rms > threshold:
                    speaking = True
                    silence = 0.0
                elif not speaking:
                    noise = noise * 0.95 + rms * 0.05
                if speaking:
                    buf.append(block)
                    if rms <= threshold:
                        silence += len(block) / SAMPLE_RATE
                    dur = len(buf) * BLOCK / SAMPLE_RATE
                    if silence >= SILENCE_END or dur >= MAX_UTTERANCE:
                        audio = np.concatenate(buf)
                        buf, speaking, silence = [], False, 0.0
                        text = self.transcribe(audio, model_name, language, prompt)
                        if text:
                            emit({"type": "final", "text": text})
                    elif time.time() - last_partial > PARTIAL_EVERY and dur > 1.0:
                        last_partial = time.time()
                        text = self.transcribe(np.concatenate(buf), model_name, language, prompt)
                        if text:
                            emit({"type": "partial", "text": text})
        if buf:
            text = self.transcribe(np.concatenate(buf), model_name, language, prompt)
            if text:
                emit({"type": "final", "text": text})
        emit({"type": "status", "state": "stopped"})
