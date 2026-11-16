"""Connected gate: real Chromium navigation to the real loopback server.

Unlike the mocked DOM suite, this test exercises actual browser HTTP,
FastAPI authentication, SQLite scoping, and UI event handling end to end.
The gate is opt-in because managed browsers can prohibit localhost access.
"""
from __future__ import annotations
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

if os.environ.get('DBOUND_BROWSER_LIVE') != '1':
    pytest.skip('Requires connected Chromium-to-local-server test host', allow_module_level=True)
playwright = pytest.importorskip('playwright.sync_api')

from decisionbound.identity import init_project
from decisionbound.settings import repository
from decisionbound.web import local_token
from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask


def test_real_browser_reads_scoped_workflow_without_execution(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path / 'private'))
    repo = tmp_path / 'repo'; repo.mkdir()
    store = repository()
    project = init_project(repo, store)
    wf = WorkflowJournal(store).create(project_path=repo,
        spec=WorkflowSpec(tasks=(WorkflowTask('read', 'prepare', 'Review local evidence'),)),
        idempotency_key='browser_sprint10')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(root/'src'), 'DBOUND_HOME':str(tmp_path/'private')}
    cmd = [sys.executable,'-m','uvicorn','decisionbound.web:create_app',
        '--factory','--host','127.0.0.1','--port',str(port),'--log-level','critical']
    process = subprocess.Popen(cmd,cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for i in range(80):
            if process.poll() is not None:
                raise AssertionError('Local web server exited during real-browser test')
            try:
                with urlopen(f'http://127.0.0.1:{port}/',timeout=.3) as response:
                    if response.status == 200: break
            except OSError:
                time.sleep(.05)
        else:
            raise AssertionError('Local web server failed to start')
        with playwright.sync_playwright() as pw:
            binary=shutil.which('chromium') or shutil.which('chromium-browser')
            browser=pw.chromium.launch(headless=True, executable_path=binary,
                                       args=['--no-sandbox','--disable-dev-shm-usage'])
            try:
                page=browser.new_page()
                errors=[]
                page.on('pageerror',lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{port}/',wait_until='domcontentloaded',timeout=10000)
                page.locator('#token').fill(local_token())
                page.locator('#project-id').fill(project.project_id)
                page.locator('#sessions-list').click()
                page.get_by_text('1 sessions found').wait_for(timeout=4000)
                assert wf in page.locator('#session-list').inner_text()
                page.locator('#session-list button').click()
                page.get_by_text('completion NOT asserted').wait_for(timeout=4000)
                assert not errors, errors
            finally:
                browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
