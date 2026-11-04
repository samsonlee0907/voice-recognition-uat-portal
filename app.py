"""Login-protected, keyless three-route recognition portal."""

import base64
import hashlib
import html
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from azure.identity.aio import AzureCliCredential, DefaultAzureCredential, ManagedIdentityCredential
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from auth_helpers import (
    acquire_connection, clear_login_attempts, developer_access_is_configured,
    hash_portal_password, login_attempt_is_allowed, login_attempts, login_form_values,
    portal_is_authenticated, portal_password_matches, record_failed_login, release_connection,
)
from portal_settings import settings
from recognition_compare import ComparisonBridge, catalog as recognition_catalog
from runtime_settings import LiveSettings, VoiceLiveSettings

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger("recognition-portal")
ROOT = Path(__file__).parent
voice_settings = VoiceLiveSettings.from_environment()
live_settings = LiveSettings.from_environment()
credential = None


@asynccontextmanager
async def lifespan(_app):
    global credential
    mode = os.getenv("AZURE_AUTH_MODE", "default")
    if mode == "cli":
        subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if not subscription:
            raise RuntimeError("AZURE_SUBSCRIPTION_ID is required for explicit CLI authentication")
        credential = AzureCliCredential(subscription=subscription)
    elif mode == "managed_identity":
        credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
    elif mode == "default":
        credential = DefaultAzureCredential()
    else:
        raise RuntimeError("AZURE_AUTH_MODE must be cli, managed_identity or default")
    try:
        yield
    finally:
        await credential.close()
        credential = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware, secret_key=settings.portal_session_secret or secrets.token_urlsafe(32),
    session_cookie="voice_live_portal", max_age=8 * 60 * 60, same_site="strict",
    https_only=not settings.local_http,
)


@app.middleware("http")
async def local_http_boundary(request: Request, call_next):
    if settings.local_http and (
        request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        return JSONResponse({"detail": "Local HTTP mode only accepts loopback clients and hosts"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    return response


def origin_is_allowed(connection: Request | WebSocket) -> bool:
    parsed = urlparse(connection.headers.get("origin", ""))
    host = connection.headers.get("host", "").lower()
    return bool(parsed.netloc) and parsed.netloc.lower() == host and not (
        parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
    ) and (parsed.scheme == "https" or parsed.scheme == "http" and settings.local_http
           and parsed.hostname in {"localhost", "127.0.0.1", "::1"})


def api_denial(request: Request):
    if not developer_access_is_configured():
        return JSONResponse({"detail": "Portal credentials are not configured"}, status_code=503)
    if not portal_is_authenticated(request):
        return JSONResponse({"detail": "Portal login required"}, status_code=401)
    return None


DESTINATIONS = {
    "/recognition": "/recognition", "/static/recognition.html": "/recognition",
    "/recognition/uat": "/recognition/uat", "/static/telecom-uat-report.html": "/recognition/uat",
}


def page_denial(request: Request):
    denial = api_denial(request)
    if denial is not None and denial.status_code == 401:
        request.session["login_destination"] = DESTINATIONS.get(request.url.path, "/recognition")
        return RedirectResponse("/login", status_code=303)
    return denial


def destination(request: Request):
    value = request.session.get("login_destination", "/recognition")
    return value if value in DESTINATIONS.values() else "/recognition"


def login_response(request: Request, error: str = "", status_code: int = 200):
    csrf = request.session.get("login_csrf")
    if not isinstance(csrf, str):
        csrf = secrets.token_urlsafe(32)
        request.session["login_csrf"] = csrf
    markup = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    markup = markup.replace("{{CSRF_TOKEN}}", html.escape(csrf, quote=True)).replace("{{LOGIN_ERROR}}", html.escape(error))
    return HTMLResponse(markup, status_code=status_code, headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    })


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.app_version, "provider_readiness": "not_checked"}


@app.get("/")
async def index():
    return RedirectResponse("/recognition", status_code=303)


@app.get("/login", response_model=None)
async def login(request: Request):
    if not developer_access_is_configured():
        return JSONResponse({"detail": "Configure portal credentials before use"}, status_code=503)
    if portal_is_authenticated(request):
        return RedirectResponse(destination(request), status_code=303)
    return login_response(request)


@app.post("/login", response_model=None)
async def login_submit(request: Request):
    if not developer_access_is_configured():
        return JSONResponse({"detail": "Configure portal credentials before use"}, status_code=503)
    ip = request.client.host if request.client else "unknown"
    if not await login_attempt_is_allowed(ip):
        return login_response(request, "Too many login attempts; please wait.", 429)
    values = await login_form_values(request)
    csrf = request.session.get("login_csrf")
    valid = False
    if values and isinstance(csrf, str):
        username, password, provided = values
        valid = (username.isascii() and provided.isascii() and secrets.compare_digest(provided, csrf)
                 and secrets.compare_digest(username, settings.portal_username)
                 and portal_password_matches(password, settings.portal_password_hash))
    if not valid:
        await record_failed_login(ip)
        return login_response(request, "Invalid username, password, or login session.", 401)
    target = destination(request)
    request.session.clear()
    request.session["portal_authenticated"] = True
    await clear_login_attempts(ip)
    return RedirectResponse(target, status_code=303)


@app.post("/logout")
async def logout(request: Request):
    if not origin_is_allowed(request):
        return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def document(filename: str, uat: bool = False):
    markup = (ROOT / "static" / filename).read_text(encoding="utf-8")
    markup = re.sub(r'\b(src|href)="(/static/[^"?]+\.(?:js|css))"',
                    lambda match: f'{match[1]}="{match[2]}?v={settings.app_version}"', markup)
    policy = ("default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; "
              "script-src 'self'; style-src 'self'; connect-src 'self'; worker-src 'self'; media-src 'self' blob:")
    if uat:
        hashes = " ".join(
            "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
            for body in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", markup, re.S))
        policy = ("default-src 'none'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; "
                  f"script-src 'self' {hashes}; style-src 'unsafe-inline'; img-src data:; "
                  "connect-src 'self'; worker-src 'self'; media-src 'self' blob:; form-action 'none'")
    return HTMLResponse(markup, headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer", "Content-Security-Policy": policy,
    })


@app.get("/recognition", response_model=None)
@app.get("/static/recognition.html", response_model=None)
async def recognition_page(request: Request):
    denial = page_denial(request)
    return denial if denial is not None else document("recognition.html")


@app.get("/recognition/uat", response_model=None)
@app.get("/static/telecom-uat-report.html", response_model=None)
async def uat_page(request: Request):
    denial = page_denial(request)
    return denial if denial is not None else document("telecom-uat-report.html", uat=True)


@app.get("/api/recognition/config", response_model=None)
async def recognition_config(request: Request):
    denial = api_denial(request)
    return denial if denial is not None else recognition_catalog(voice_settings, live_settings)


async def access_token():
    if credential is None:
        raise RuntimeError("Azure credential lifecycle has not started")
    return (await credential.get_token("https://ai.azure.com/.default")).token


@app.websocket("/ws/recognition")
async def recognition_socket(websocket: WebSocket):
    if not developer_access_is_configured() or not portal_is_authenticated(websocket) or not origin_is_allowed(websocket):
        await websocket.close(code=1008, reason="Same-origin portal login required")
        return
    ip = websocket.client.host if websocket.client else "unknown"
    if not await acquire_connection(ip):
        await websocket.close(code=1013, reason="Connection limit reached")
        return
    try:
        await websocket.accept()
        await ComparisonBridge(websocket, voice_settings, live_settings, access_token).run()
    except WebSocketDisconnect:
        logger.info("Recognition browser disconnected")
    finally:
        await release_connection(ip)
        if websocket.client_state.name == "CONNECTED" and websocket.application_state.name == "CONNECTED":
            await websocket.close()


ASSETS = {"recognition.js", "recognition.css", "style.css", "airline-service-worklet.js", "login.css"}


@app.get("/static/{asset:path}", response_model=None)
async def static_asset(asset: str):
    if asset not in ASSETS:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(ROOT / "static" / asset)
