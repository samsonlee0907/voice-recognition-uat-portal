"""Opt-in local browser checks. No cloud connections or caller recordings."""

import json
import os
import secrets
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app


@unittest.skipUnless(os.getenv("RUN_PORTAL_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import asyncio
        import uvicorn
        from playwright.sync_api import sync_playwright

        cls.original = {name: getattr(app.settings, name) for name in (
            "portal_username", "portal_password_hash", "portal_session_secret", "local_http")}
        cls.password = secrets.token_urlsafe(24)
        app.settings.portal_username = "browser-test"
        app.settings.portal_password_hash = app.hash_portal_password(cls.password)
        app.settings.portal_session_secret = secrets.token_urlsafe(48)
        app.settings.local_http = True
        app.login_attempts.clear()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        cls.url = f"http://localhost:{listener.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(app.app, log_level="error", ws_max_size=131072))

        def serve():
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(cls.server.serve(sockets=[listener]))

        cls.thread = threading.Thread(target=serve, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(.05)
        if not cls.server.started:
            raise RuntimeError("Browser validation server did not start")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.should_exit = True
        cls.thread.join(10)
        for name, value in cls.original.items():
            setattr(app.settings, name, value)
        if cls.thread.is_alive():
            raise RuntimeError("Browser validation server did not stop")

    def test_prompt_bank_saved_records_and_portable_offline_export(self):
        from playwright.sync_api import expect

        context = self.browser.new_context(accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(self.url + "/recognition/uat")
        page.locator("#username").fill("browser-test")
        page.locator("#password").fill(self.password)
        page.locator('button[type="submit"]').click()
        page.wait_for_url(self.url + "/recognition/uat")
        self.assertFalse(any("/api/recognition" in item for item in requests))
        bank = json.loads(page.locator("#uat-bank").text_content())
        zodiac = set()
        for item in bank["cases"]:
            page.locator("#uat-case").select_option(item["id"])
            prompt = page.locator("#realtime-instructions").input_value()
            self.assertIn("2026-11-04.2", prompt)
            self.assertLessEqual(len(prompt), 4000)
            self.assertIn(bank["groups"][item["group"]]["question"], prompt)
            if item["group"] == "zodiac":
                zodiac.add(prompt)
        self.assertEqual(len(zodiac), 1)
        page.locator("#uat-case").select_option("DOB-02")
        self.assertNotIn("2000-02-29", page.locator("#realtime-instructions").input_value())
        self.assertNotIn("民國八十九", page.locator("#realtime-instructions").input_value())
        page.locator("#uat-add-manual").click()
        for lane in ("speech", "realtime", "live"):
            page.locator("#" + lane + "-grade").select_option("not_run")
        page.locator("#uat-run-note").fill("Synthetic note <img src=x onerror=alert(1)>")
        page.locator("#uat-save-run").click()
        expect(page.locator("#uat-record-summary")).to_contain_text("1 / 100")
        page.evaluate("""() => {
          document.getElementById("realtime-instructions").value = "UNSAVED_PRIVATE_DRAFT";
          document.getElementById("live-transcript").textContent = "UNSAVED_LIVE_TEXT";
        }""")
        with page.expect_download() as downloaded:
            page.locator("#export-html").click()
        with tempfile.TemporaryDirectory(prefix="recognition-uat-offline-") as directory:
            target = Path(directory) / "portable.html"
            downloaded.value.save_as(target)
            exported = target.read_text(encoding="utf-8")
            self.assertNotIn("UNSAVED_PRIVATE_DRAFT", exported)
            self.assertNotIn("UNSAVED_LIVE_TEXT", exported)
            offline = context.new_page()
            offline_requests = []
            offline.on("request", lambda request: offline_requests.append(request.url))
            offline.goto(target.as_uri())
            self.assertTrue(offline.locator("#uat-enable").is_disabled())
            self.assertTrue(offline.locator("#compare-start").is_disabled())
            expect(offline.locator("#uat-record-summary")).to_contain_text("1 / 100")
            self.assertEqual(offline.locator("#uat-records img").count(), 0)
            self.assertFalse(any(item.startswith(("https:", "http:")) for item in offline_requests))
            self.assertEqual(offline.locator("#hosted-link").get_attribute("href"), self.url + "/recognition/uat")
        page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.assertFalse(errors)
