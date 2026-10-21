"""Local speech-to-text with open-source Whisper (faster-whisper). Audio never leaves the machine and is not stored."""
from __future__ import annotations

import io
import threading

from ..config import settings

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 90
_model = None
_lock = threading.Lock()


class VoiceUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise VoiceUnavailable("Voice input is not installed (pip install -r requirements-voice.txt).") from exc
                _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8",
                                      download_root=str(settings.data_dir / "models"))
    return _model


def transcribe(data: bytes) -> dict:
    if not data:
        raise VoiceUnavailable("No audio was received.")
    if len(data) > MAX_AUDIO_BYTES:
        raise VoiceUnavailable("The recording is too long. Keep voice commands under a minute.")
    model = _get_model()
    try:
        segments, info = model.transcribe(io.BytesIO(data), beam_size=1, vad_filter=True)
        parts, total = [], 0.0
        for s in segments:
            parts.append(s.text.strip())
            total = s.end
            if total > MAX_SECONDS:
                break
    except Exception as exc:  # undecodable audio
        raise VoiceUnavailable("The recording could not be understood as audio.") from exc
    return {"text": " ".join(p for p in parts if p).strip(), "language": info.language,
            "seconds": round(float(info.duration), 1)}
