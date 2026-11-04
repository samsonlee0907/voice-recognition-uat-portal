import base64
import hashlib
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app
from runtime_settings import LiveSettings, VoiceLiveSettings

ROOT = Path(__file__).parents[1]


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.original = {name: getattr(app.settings, name) for name in (
            "portal_username", "portal_password_hash", "portal_session_secret", "local_http")}
        app.settings.portal_username = "test-developer"
        app.settings.portal_password_hash = app.hash_portal_password("synthetic-test-password")
        app.settings.portal_session_secret = "test-only-signing-secret-not-a-real-credential"
        app.settings.local_http = False
        app.login_attempts.clear()
        self.client = TestClient(app.app, base_url="https://localhost")

    def tearDown(self):
        self.client.close()
        for name, value in self.original.items():
            setattr(app.settings, name, value)
        app.login_attempts.clear()

    def login(self):
        page = self.client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        self.assertIn("httponly", page.headers["set-cookie"].lower())
        self.assertIn("secure", page.headers["set-cookie"].lower())
        return self.client.post("/login", data={
            "username": "test-developer", "password": "synthetic-test-password", "csrf_token": token,
        }, follow_redirects=False)

    def test_authentication_static_aliases_and_destination(self):
        for path in ("/recognition", "/recognition/uat", "/static/recognition.html",
                     "/static/telecom-uat-report.html"):
            self.client.cookies.clear()
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 303)
            self.assertEqual(self.client.get("/api/recognition/config").status_code, 401)
            self.assertEqual(self.login().status_code, 303)
            self.assertEqual(self.client.get(path).status_code, 200)
        for path in ("/static/TELECOM-UAT-REPORT.HTML", "/static//telecom-uat-report.html",
                     "/static/%5ctelecom-uat-report.html", "/static/uat-app.js", "/static/uat-template.html",
                     "/airline", "/airline/service", "/developer", "/static/login.html"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 404)
        self.assertEqual(self.client.get("/static/login.css").status_code, 200)
        app.settings.portal_username = None
        self.assertEqual(self.client.get("/login").status_code, 503)
        self.assertEqual(self.client.get("/recognition/uat").status_code, 503)

    def test_csrf_and_failed_login_limit(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/login", data={
                "username": "test-developer", "password": "synthetic-test-password", "csrf_token": "wrong",
            }).status_code, 401)
        self.assertEqual(self.client.post("/login", data={}).status_code, 429)

    def test_microphone_worklet_references_are_served(self):
        script = self.client.get("/static/recognition.js")
        self.assertEqual(script.status_code, 200)
        paths = re.findall(r'addModule\("([^"]+)"\)', script.text)
        self.assertTrue(paths)
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('registerProcessor("airline-service-processor"', response.text)

    def test_websocket_requires_login_and_matching_origin(self):
        for origin in ("https://attacker.example", "https://localhost"):
            with self.assertRaises(WebSocketDisconnect) as error:
                with self.client.websocket_connect("/ws/recognition", headers={"origin": origin}):
                    pass
            self.assertEqual(error.exception.code, 1008)
        self.login()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("wss://localhost/ws/recognition", headers={"origin": "http://localhost.evil.example"}):
                pass
        with self.client.websocket_connect("wss://localhost/ws/recognition", headers={"origin": "https://localhost"}) as socket:
            self.assertEqual(socket.receive_json()["type"], "compare.ready")
            socket.send_json({"type": "compare.stop"})
            self.assertEqual(socket.receive_json()["type"], "compare.ended")

    def test_csp_covers_inline_scripts_without_unsafe_eval_or_public_report(self):
        self.login()
        response = self.client.get("/recognition/uat")
        self.assertEqual(response.headers["cache-control"], "no-store")
        policy = response.headers["content-security-policy"]
        script_policy = policy.split("script-src ", 1)[1].split(";")[0]
        self.assertNotIn("'unsafe-inline'", script_policy)
        self.assertNotIn("'unsafe-eval'", script_policy)
        self.assertIn("frame-ancestors 'none'", policy)
        for body in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", response.text, re.S):
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            self.assertIn("'sha256-" + digest + "'", script_policy)

    def test_only_synthetic_bank_and_no_answer_leakage_in_prompt_builder(self):
        bank = json.loads((ROOT / "config" / "telecom_uat_cases.json").read_text(encoding="utf-8"))
        markup = (ROOT / "static" / "telecom-uat-report.html").read_text(encoding="utf-8")
        embedded = json.loads(re.search(r'<script id="uat-bank" type="application/json">(.*?)</script>', markup, re.S)[1])
        self.assertEqual(bank, embedded)
        self.assertEqual(len(bank["cases"]), 56)
        self.assertTrue(all(not case["refs"] for case in bank["cases"]))
        self.assertNotIn('id="report-data"', markup)
        self.assertNotRegex(markup, r"202610\d\d-\d\d\.log|azurecontainerapps\.io|sourceDigest")
        prompt = markup.split("  function prompts() {", 1)[1].split("  function applyPrompts()", 1)[0]
        self.assertIn("2026-11-04.2", prompt)
        for value in ("selected.expected", "selected.utterance", "selected.id"):
            self.assertNotIn(value, prompt)
        from tools.build_uat import build
        before = (ROOT / "static" / "telecom-uat-report.html").read_bytes()
        build()
        self.assertEqual(before, (ROOT / "static" / "telecom-uat-report.html").read_bytes())

    def test_resource_roots_reject_credentials_paths_and_untrusted_hosts(self):
        for name, factory in (("VOICE_LIVE_ENDPOINT", VoiceLiveSettings), ("GPT_LIVE_ENDPOINT", LiveSettings)):
            for value in ("http://example.openai.azure.com", "https://example.openai.azure.com/anything",
                          "https://example.openai.azure.com.evil.example", "https://user:secret@example.openai.azure.com"):
                with patch.dict("os.environ", {name: value}):
                    with self.assertRaises(RuntimeError):
                        factory.from_environment()
        self.assertFalse(app.origin_is_allowed(self.client.build_request("GET", "https://localhost")))
