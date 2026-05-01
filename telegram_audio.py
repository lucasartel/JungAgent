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
OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"


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


def audio_reply_enabled() -> bool:
    return _truthy(os.getenv("TELEGRAM_AUDIO_REPLY_ENABLED"), default=True)


def audio_reply_model() -> str:
    return os.getenv("TELEGRAM_AUDIO_REPLY_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")


def audio_reply_voice() -> str:
    return os.getenv("TELEGRAM_AUDIO_REPLY_VOICE", "nova").strip() or "nova"


def audio_reply_format() -> str:
    value = os.getenv("TELEGRAM_AUDIO_REPLY_FORMAT", "mp3").strip().lower()
    return value if value in {"mp3", "pcm"} else "mp3"


def audio_reply_max_chars() -> int:
    raw = os.getenv("TELEGRAM_AUDIO_REPLY_MAX_CHARS", "1800")
    try:
        return max(200, int(raw))
    except (TypeError, ValueError):
        return 1800


def audio_reply_timeout() -> float:
    raw = os.getenv("TELEGRAM_AUDIO_REPLY_TIMEOUT_SECONDS", "45")
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return 45.0


@dataclass
class AudioTranscriptionResult:
    transcript: str
    model: str
    raw_response_id: Optional[str] = None


@dataclass
class AudioSpeechResult:
    audio_bytes: bytes
    model: str
    content_type: str
    response_format: str
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


class TelegramAudioSpeaker:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.model = audio_reply_model()
        self.voice = audio_reply_voice()
        self.response_format = audio_reply_format()
        self.timeout = audio_reply_timeout()

    def available(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str) -> AudioSpeechResult:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY nao configurada para gerar resposta em audio.")

        clean_text = " ".join((text or "").split())
        if not clean_text:
            raise ValueError("Texto vazio recebido para sintese de voz.")

        max_chars = audio_reply_max_chars()
        if len(clean_text) > max_chars:
            clean_text = clean_text[: max_chars - 3].rstrip(" ,.;:") + "..."

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("PUBLIC_SITE_URL", "https://jungagent.org"),
            "X-Title": "JungAgent Telegram Audio",
        }
        payload = {
            "model": self.model,
            "input": clean_text,
            "voice": self.voice,
            "response_format": self.response_format,
            "speed": 1,
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(OPENROUTER_TTS_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                detail = response.text[:800] if response.text else ""
                raise RuntimeError(f"OpenRouter TTS HTTP {response.status_code}: {detail}")

        audio_bytes = response.content or b""
        if not audio_bytes:
            raise RuntimeError("OpenRouter retornou audio vazio.")

        return AudioSpeechResult(
            audio_bytes=audio_bytes,
            model=self.model,
            content_type=response.headers.get("Content-Type", "audio/mpeg"),
            response_format=self.response_format,
            raw_response_id=response.headers.get("X-Generation-Id"),
        )
