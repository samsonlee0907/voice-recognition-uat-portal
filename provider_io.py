"""Provider protocol helpers retained from the original application."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp
import websockets
from fastapi import WebSocketDisconnect
from websockets.exceptions import WebSocketException
from runtime_settings import SERVICE, LiveSettings, VoiceLiveSettings, transcription_profile
from errors import CaptureError

logger = logging.getLogger('recognition-provider')
provider_logger = wire_logger = logging.getLogger('recognition-wire')
wire_logger.setLevel(logging.WARNING)
MAX_MESSAGE_BYTES = 128 * 1024
MAX_INSTRUCTIONS = 4000

class ProviderFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProviderSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


TokenProvider = Callable[[], Awaitable[str]]


Connector = Callable[[str, str], Awaitable[ProviderSocket]]


async def connect_provider(url: str, token: str) -> ProviderSocket:
    return await websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {token}"},
        max_size=MAX_MESSAGE_BYTES, open_timeout=15, close_timeout=3,
        ping_interval=20, ping_timeout=20,
        logger=provider_logger,
    )


def event_object(raw: str | bytes) -> dict[str, Any]:
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderFailure("invalid_provider_event") from exc
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ProviderFailure("invalid_provider_event")
    if event["type"] == "error":
        raise ProviderFailure("provider_rejected")
    return event


def provider_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderFailure("invalid_provider_event")
    return value


def guide_event(event: dict[str, Any]) -> dict[str, Any]:
    delta, start, end = event.get("delta"), event.get("start_ms"), event.get("end_ms")
    if (
        not isinstance(delta, str) or not delta
        or type(start) not in {int, float} or type(end) not in {int, float}
        or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end
    ):
        raise ProviderFailure("invalid_provider_event")
    if event["type"] == "session.output_audio.delta":
        try:
            decoded = base64.b64decode(delta, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProviderFailure("invalid_provider_event") from exc
        if not decoded or len(decoded) % 2:
            raise ProviderFailure("invalid_provider_event")
    elif len(delta) > 512:
        raise ProviderFailure("invalid_provider_event")
    return {"type": event["type"], "delta": delta, "start_ms": start, "end_ms": end}


def guide_usage(event: dict[str, Any]) -> dict[str, int | float]:
    seconds = provider_mapping(event.get("usage")).get("seconds")
    if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0:
        raise ProviderFailure("invalid_provider_event")
    return {"seconds": seconds}


async def wait_ready(socket: ProviderSocket, kind: str, timeout: float = 20) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            event = event_object(await socket.recv())
            if event["type"] == kind:
                return event
async def connect_voice_live(url: str, token: str, *, max_size: int = 128 * 1024) -> ProviderSocket:
    return await websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {token}"}, max_size=max_size,
        open_timeout=15, close_timeout=3, ping_interval=20, ping_timeout=20, logger=wire_logger,
    )


async def cancel_task(task: asyncio.Task | None) -> None:
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except WebSocketDisconnect:
            logger.debug("Retired task after airline browser disconnected")
def valid_sdp(value: object) -> bool:
    return (
        isinstance(value, str) and value.isascii() and "\0" not in value and 0 < len(value) <= 65536
        and value.startswith("v=0") and "\nm=audio " in value
    )


async def create_live_rtc(
    http: aiohttp.ClientSession, settings: LiveSettings, token: TokenProvider,
    configuration: dict, offer: str, connector: Connector = connect_provider,
    *, answer_ready: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[ProviderSocket, str, dict]:
    started = time.monotonic()
    if not settings.endpoint or not valid_sdp(offer):
        raise CaptureError("invalid_command")
    async with http.post(
        settings.endpoint + "/openai/v1/live/sessions",
        headers={"Authorization": "Bearer " + await token()},
        json={"session": configuration, "transport": {"type": "webrtc", "sdp": offer}},
        allow_redirects=False,
    ) as response:
        if response.status not in {200, 201}:
            logger.warning("GPT-Live WebRTC creation rejected: HTTP %d", response.status)
            raise ProviderFailure("provider_rejected")
        body = bytearray()
        async for chunk in response.content.iter_chunked(8192):
            body.extend(chunk)
            if len(body) > 128 * 1024:
                raise ProviderFailure("invalid_provider_event")
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderFailure("invalid_provider_event") from exc
    document = provider_mapping(document)
    session_id = provider_mapping(document.get("session")).get("id")
    transport = provider_mapping(document.get("transport"))
    answer = transport.get("sdp")
    if (
        not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id)
        or transport.get("type") != "webrtc" or not valid_sdp(answer)
    ):
        raise ProviderFailure("invalid_provider_event")
    if answer_ready:
        await answer_ready(answer)
    socket = await connector(settings.url() + "/" + quote(session_id, safe="") + "/attach", await token())
    logger.info("GPT-Live WebRTC startup: phase=attached elapsed_ms=%d", int((time.monotonic() - started) * 1000))
    accepted_session = False
    try:
        # Sideband attachment can miss startup; a sparse no-op requests a fresh public snapshot.
        await socket.send(json.dumps({"type": "session.update", "session": {}}))
        accepted = await wait_ready(socket, "session.updated", timeout=45)
        public_session = provider_mapping(accepted.get("session"))
        if public_session.get("id") != session_id:
            raise ProviderFailure("provider_rejected")
        logger.info("GPT-Live WebRTC startup: phase=verified elapsed_ms=%d", int((time.monotonic() - started) * 1000))
        accepted_session = True
        return socket, answer, public_session
    finally:
        if not accepted_session:
            try:
                await socket.send(json.dumps({"type": "session.close"}))
                await wait_ready(socket, "session.closed", timeout=3)
            except (ProviderFailure, WebSocketException, OSError, TimeoutError):
                logger.warning("GPT-Live failed startup final usage not confirmed")
            await socket.close()
def checked_instructions(value: object) -> str:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > MAX_INSTRUCTIONS
        or any(
            ord(character) < 32 and character not in "\n\r\t" or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise CaptureError("invalid_agent_instructions")
    return value.strip()
