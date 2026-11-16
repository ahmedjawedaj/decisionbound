"""Opt-in real HTTP/Node client against loopback Uvicorn (not VS Code host)."""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
if os.environ.get('DBOUND_LOOPBACK_HTTP_TEST') != '1':
    pytest.skip('Opt-in real loopback HTTP integration test', allow_module_level=True)

from decisionbound.identity import init_project
from decisionbound.settings import repository
from decisionbound.web import local_token
from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask


def test_node_client_against_real_authenticated_uvicorn(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path/'private'))
    store = repository()
    repo = tmp_path / 'repo'; repo.mkdir()
    project = init_project(repo, store)
    wf = WorkflowJournal(store).create(project_path=repo,
        spec=WorkflowSpec(tasks=(WorkflowTask('read', 'prepare', 'Check source metadata'),)),
        idempotency_key='realnode_12345678')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port=sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'DBOUND_HOME': str(tmp_path/'private'),
           'PYTHONPATH': str(root/'src'), 'DBOUND_TEST_TOKEN': local_token(),
           'DBOUND_TEST_PROJECT': project.project_id, 'DBOUND_TEST_WF': wf,
           'DBOUND_TEST_PORT': str(port)}
    cmd = [sys.executable, '-m', 'uvicorn', 'decisionbound.web:create_app',
           '--factory', '--host', '127.0.0.1', '--port', str(port), '--log-level', 'critical']
    server = subprocess.Popen(cmd, cwd=root, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        ready = False
        for _ in range(50):
            if server.poll() is not None: break
            try:
                with urlopen(Request(f'http://127.0.0.1:{port}/api/client/v1/capabilities',
                    headers={'Authorization':'Bearer ' + env['DBOUND_TEST_TOKEN']}), timeout=.3) as resp:
                    ready = resp.status == 200
                if ready: break
            except OSError:
                time.sleep(.06)
        assert ready, 'Local API failed to start or localhost access was blocked'
        script = """const {LocalControlClient}=require('./apps/vscode/src/client');
        (async()=>{const c=new LocalControlClient({baseUrl:'http://127.0.0.1:'+process.env.DBOUND_TEST_PORT,
         token:process.env.DBOUND_TEST_TOKEN});await c.capabilities();
         const x=await c.sessions(process.env.DBOUND_TEST_PROJECT);
         if(x.sessions.length!==1||x.sessions[0].session_id!==process.env.DBOUND_TEST_WF)throw Error('Wrong sessions');
         const y=await c.snapshot(process.env.DBOUND_TEST_PROJECT,process.env.DBOUND_TEST_WF);
         if(y.task_completed!==false)throw Error('Unverified completion claim');
         const z=await c.events(process.env.DBOUND_TEST_PROJECT,process.env.DBOUND_TEST_WF);
         if(z.events.length!==1||z.events[0].event!=='created')throw Error('Missing canonical event');
         process.stdout.write(JSON.stringify({session_id:y.workflow_id,event_id:z.next_after}));
        })().catch(e=>{process.stderr.write(e.message);process.exitCode=1});"""
        proc = subprocess.run(['node', '-e', script], cwd=root, env=env,
                              capture_output=True, text=True, timeout=20)
        assert proc.returncode == 0, proc.stderr
        payload=json.loads(proc.stdout)
        assert payload['session_id'] == wf and payload['event_id'] >= 1
    finally:
        server.terminate()
        try: server.wait(timeout=5)
        except subprocess.TimeoutExpired: server.kill();server.wait(timeout=5)
