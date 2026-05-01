"""
telegram_audio.py

Small OpenRouter-backed speech-to-text helper for Telegram audio messages.
It keeps audio handling technical and stateless: callers pass bytes, receive
a transcript plus audit metadata, and decide how to route the text.
"""

import base64
import os
from dataclasses import dataclass
from typing import Optional

import httpx


OPENROUTER_STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


def _truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def audio_input_enabled() -> bool:
    return _truthy(os.getenv("TELEGRAM_AUDIO_INPUT_ENABLED"), default=True)


def audio_max_bytes() -> int:
    raw = os.getenv("TELEGRAM_AUDIO_MAX_BYTES", "20000000")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 20_000_000


def audio_transcription_model() -> str:
    return os.getenv("TELEGRAM_AUDIO_TRANSCRIPTION_MODEL", "openai/gpt-4o-transcribe")


def audio_transcription_timeout() -> float:
    raw = os.getenv("TELEGRAM_AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS", "45")
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return 45.0


def audio_transcription_language() -> str:
    return os.getenv("TELEGRAM_AUDIO_LANGUAGE", "pt").strip() or "pt"


@dataclass
class AudioTranscriptionResult:
    transcript: str
    model: str
    raw_response_id: Optional[str] = None


class TelegramAudioTranscriber:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.model = audio_transcription_model()
        self.timeout = audio_transcription_timeout()

    def available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio_bytes: bytes, audio_format: str = "ogg") -> AudioTranscriptionResult:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY nao configurada para transcricao de audio.")
        if not audio_bytes:
            raise ValueError("Audio vazio recebido para transcricao.")

        encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("PUBLIC_SITE_URL", "https://jungagent.org"),
            "X-Title": "JungAgent Telegram Audio",
        }
        payload = {
            "model": self.model,
            "input_audio": {
                "data": encoded_audio,
                "format": audio_format or "ogg",
            },
            "language": audio_transcription_language(),
            "temperature": 0,
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(OPENROUTER_STT_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                detail = response.text[:800] if response.text else ""
                raise RuntimeError(f"OpenRouter STT HTTP {response.status_code}: {detail}")
            data = response.json()

        transcript = (data.get("text") or "").strip()
        if not transcript:
            raise RuntimeError("OpenRouter retornou transcricao vazia.")

        return AudioTranscriptionResult(
            transcript=transcript,
            model=data.get("model") or self.model,
            raw_response_id=data.get("id") or response.headers.get("X-Generation-Id"),
        )
