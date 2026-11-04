"""Original bounded developer-login and admission primitives."""

import asyncio
import hashlib
import secrets
from urllib.parse import parse_qs
from collections import defaultdict, deque
from fastapi import Request, WebSocket

from portal_settings import settings

PBKDF2_ITERATIONS = 600_000
PBKDF2_MIN_ITERATIONS = 100_000
PBKDF2_MAX_ITERATIONS = 1_000_000
MAX_LOGIN_ATTEMPTS_PER_WINDOW = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 600
MAX_LOGIN_FORM_BYTES = 2048
MAX_CONNECTIONS_PER_WINDOW = 12
CONNECTION_WINDOW_SECONDS = 600
MAX_ACTIVE_CONNECTIONS_PER_IP = 2
MAX_ACTIVE_CONNECTIONS = 8
login_attempts = defaultdict(deque)
connection_attempts = defaultdict(deque)
active_connections = defaultdict(int)
login_lock = asyncio.Lock()
admission_lock = asyncio.Lock()

def hash_portal_password(
    password: str, *, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS
) -> str:
    if not PBKDF2_MIN_ITERATIONS <= iterations <= PBKDF2_MAX_ITERATIONS:
        raise ValueError("PBKDF2 iterations are outside the supported range")
    if salt is None:
        salt = secrets.token_bytes(16)
    if len(salt) < 16:
        raise ValueError("PBKDF2 salt must be at least 16 bytes")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def portal_password_matches(password: str, stored_hash: str | None) -> bool:
    if not stored_hash or len(password) > 256:
        return False
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = stored_hash.split("$")
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected_digest = bytes.fromhex(raw_digest)
    except (TypeError, ValueError):
        return False
    if (
        algorithm != "pbkdf2_sha256"
        or not PBKDF2_MIN_ITERATIONS <= iterations <= PBKDF2_MAX_ITERATIONS
        or len(salt) < 16
        or len(expected_digest) != 32
    ):
        return False
    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(actual_digest, expected_digest)


def portal_is_authenticated(connection: Request | WebSocket) -> bool:
    return settings.portal_login_enabled and connection.session.get("portal_authenticated") is True


def developer_access_is_configured() -> bool:
    return settings.portal_login_enabled and bool(
        settings.portal_username and settings.portal_password_hash and settings.portal_session_secret
    )


async def login_attempt_is_allowed(ip_address: str) -> bool:
    now = asyncio.get_running_loop().time()
    async with login_lock:
        attempts = login_attempts[ip_address]
        while attempts and attempts[0] <= now - LOGIN_ATTEMPT_WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) < MAX_LOGIN_ATTEMPTS_PER_WINDOW


async def record_failed_login(ip_address: str) -> None:
    now = asyncio.get_running_loop().time()
    async with login_lock:
        login_attempts[ip_address].append(now)


async def clear_login_attempts(ip_address: str) -> None:
    async with login_lock:
        login_attempts.pop(ip_address, None)


async def login_form_values(request: Request) -> tuple[str, str, str] | None:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return None
    body = await request.body()
    if len(body) > MAX_LOGIN_FORM_BYTES:
        return None
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        username = fields.get("username")
        password = fields.get("password")
        csrf_token = fields.get("csrf_token")
    except (UnicodeDecodeError, ValueError):
        return None
    if not (
        isinstance(username, list)
        and isinstance(password, list)
        and isinstance(csrf_token, list)
        and len(username) == len(password) == len(csrf_token) == 1
    ):
        return None
    return username[0], password[0], csrf_token[0]


async def acquire_connection(ip_address: str) -> bool:
    now = asyncio.get_running_loop().time()
    async with admission_lock:
        attempts = connection_attempts[ip_address]
        while attempts and attempts[0] <= now - CONNECTION_WINDOW_SECONDS:
            attempts.popleft()
        if (
            len(attempts) >= MAX_CONNECTIONS_PER_WINDOW
            or active_connections[ip_address] >= MAX_ACTIVE_CONNECTIONS_PER_IP
            or sum(active_connections.values()) >= MAX_ACTIVE_CONNECTIONS
        ):
            return False
        attempts.append(now)
        active_connections[ip_address] += 1
        return True


async def release_connection(ip_address: str) -> None:
    async with admission_lock:
        if active_connections[ip_address] <= 1:
            active_connections.pop(ip_address, None)
        else:
            active_connections[ip_address] -= 1
