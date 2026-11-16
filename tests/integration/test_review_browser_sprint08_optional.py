"""Opt-in real Chromium test of the local review UI, not a fake DOM.

Run with DBOUND_BROWSER_HTTP_TEST=1 and a system Chromium installation, or on an
explicit browser-enabled CI runner. No remote model or network access needed.
"""
from __future__ import annotations

import os
import shutil
import socket
import threading
import time
import json

import pytest

if os.getenv("DBOUND_BROWSER_HTTP_TEST") != "1":
    pytest.skip("Opt-in Chromium test requires DBOUND_BROWSER_HTTP_TEST=1", allow_module_level=True)
playwright = pytest.importorskip("playwright.sync_api")
import uvicorn
from decisionbound.web import create_app, local_token

from .test_review_gate_sprint08 import completed  # pytest fixture with deterministic mock patch


def test_browser_shows_safe_diff_and_requires_confirmation(completed):
    _, _, root, wid = completed
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                          log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(70):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=executable, headless=True,
                                          args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page()
                errors=[]
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                page.locator("#token").fill(local_token())
                page.locator("#workflow").fill(wid)
                page.locator("#review-load").click()
                page.get_by_text("Review metadata loaded.").wait_for()
                assert "VALUE = 2" not in page.locator("#review-metadata").inner_text()
                assert "Patch preview is opt-in." in page.locator("#review-diff").inner_text()
                page.locator("#review-approve").click()
                page.get_by_text("Action refused:").wait_for()
                page.locator("#review-preview").click()
                page.locator("#review-diff").get_by_text("VALUE = 2").wait_for()
                page.locator("#review-confirm").check()
                page.locator("#review-approve").click()
                page.get_by_text("unverified tests is required").wait_for()
                page.locator("#review-unverified").check()
                page.locator("#review-approve").click()
                page.get_by_text("Recorded approve_export at revision 1").wait_for()
                assert "VALUE = 1" == (root / "app.py").read_text().strip()
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=8)
