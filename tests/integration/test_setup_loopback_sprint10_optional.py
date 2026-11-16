"""Opt-in real Node.js HTTP client to Uvicorn for end-user profile readiness."""
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

if os.getenv('DBOUND_LOOPBACK_HTTP_TEST') != '1':
    pytest.skip('Connected Node HTTP setup test is opt-in', allow_module_level=True)

from decisionbound.local_auth import local_token
from decisionbound.profiles import ProfileManager
from decisionbound.settings import repository


def test_setup_changes_in_python_are_immediately_visible_to_real_node_http(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME',str(tmp_path/'private'))
    store=repository()
    ProfileManager(store).use('openrouter')
    token=local_token()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    root=Path(__file__).resolve().parents[2]
    env={**os.environ,'DBOUND_HOME':str(tmp_path/'private'),'PYTHONPATH':str(root/'src'),
         'DBOUND_TEST_TOKEN':token,'DBOUND_TEST_PORT':str(port)}
    server=subprocess.Popen([sys.executable,'-m','uvicorn','decisionbound.web:create_app',
        '--factory','--host','127.0.0.1','--port',str(port),'--log-level','critical'],
        cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(60):
            if server.poll() is not None:
                raise AssertionError('Local API exited during setup transport test')
            try:
                request=Request(f'http://127.0.0.1:{port}/api/setup/v1/status',
                                headers={'Authorization':'Bearer '+token})
                with urlopen(request,timeout=.3) as result:
                    if result.status==200:break
            except OSError:
                time.sleep(.05)
        else:raise AssertionError('Local setup API failed to start')
        script="""const {LocalControlClient}=require('./apps/vscode/src/client');
        (async()=>{const c=new LocalControlClient({baseUrl:'http://127.0.0.1:'+process.env.DBOUND_TEST_PORT,
          token:process.env.DBOUND_TEST_TOKEN});await c.capabilities();
          const report=await c.setupStatus();
          if(report.profile!=='openrouter'||report.integration_tested!==false)throw Error('Bad setup snapshot');
          process.stdout.write(JSON.stringify({profile:report.profile,checks:report.checks.length}));
        })().catch(e=>{process.stderr.write(e.message);process.exitCode=1});"""
        proc=subprocess.run(['node','-e',script],cwd=root,env=env,text=True,
                            capture_output=True,timeout=15)
        assert proc.returncode == 0,proc.stderr
        assert json.loads(proc.stdout)['profile']=='openrouter'
        assert token not in proc.stdout
    finally:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
