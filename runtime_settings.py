"""Independent Azure endpoints; Voice Live uses managed model identifiers."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

from errors import CaptureError

SERVICE = json.loads((Path(__file__).parent / "config" / "recognition.json").read_text(encoding="utf-8"))
if (SERVICE.get("model") != "gpt-6-luna" or SERVICE.get("native_realtime_model") != "gpt-realtime-2.1"
        or SERVICE.get("api_version") != "2026-07-15"):
    raise RuntimeError("The configured model/API combination has not been qualified; no fallback is allowed")


def endpoint(name: str, suffixes: tuple[str, ...]) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(suffixes)
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise RuntimeError(f"{name} must be an Azure resource HTTPS root, without a path or credentials")
    return value.rstrip("/")


@dataclass(frozen=True)
class VoiceLiveSettings:
    endpoint: str | None

    @classmethod
    def from_environment(cls):
        return cls(endpoint("VOICE_LIVE_ENDPOINT", (".cognitiveservices.azure.com", ".services.ai.azure.com")))

    def url(self) -> str:
        if not self.endpoint:
            raise CaptureError("not_configured")
        return self.endpoint.replace("https://", "wss://", 1) + "/voice-live/realtime?" + urlencode({
            "api-version": SERVICE["api_version"], "model": SERVICE["model"],
        })


@dataclass(frozen=True)
class LiveSettings:
    endpoint: str | None
    deployment: str = "gpt-live-1"

    @classmethod
    def from_environment(cls):
        deployment = os.getenv("GPT_LIVE_DEPLOYMENT", "gpt-live-1")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", deployment):
            raise RuntimeError("GPT_LIVE_DEPLOYMENT must be a deployment name, not a URL")
        return cls(endpoint("GPT_LIVE_ENDPOINT", (".openai.azure.com",)), deployment)

    def url(self) -> str:
        if not self.endpoint:
            raise CaptureError("not_configured")
        return self.endpoint.replace("https://", "wss://", 1) + "/openai/v1/live/sessions"


@dataclass(frozen=True)
class TranscriptionProfile:
    def configuration(self) -> dict:
        return {"model": "azure-speech", "language": "zh-TW"}


def transcription_profile(identifier: str) -> TranscriptionProfile:
    if identifier != "speech-zhtw":
        raise CaptureError("invalid_transcription_profile")
    return TranscriptionProfile()
