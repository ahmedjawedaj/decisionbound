"""Shared CLI and localhost API state for repaired coding sessions."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from click import unstyle
from typer.testing import CliRunner
from decisionbound.cli import app
from tests.integration.test_code_workflow import project, PATCH


def test_cli_mock_repair_and_durable_status(project, monkeypatch, tmp_path):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({
        'schema_version':'1.0', 'contract_id':'cli-fixture',
        'allowed_paths':['app.py'], 'required_paths':['app.py'],
        'test_commands':[['python','-m','pytest','-q']],
        'assertions':[{'path':'app.py','contains':'VALUE = 2','excludes':''}]
    }))
    patch = tmp_path / 'patch.diff';patch.write_text(PATCH)
    runner = CliRunner()
    cmd = ['code', 'repair-mock', 'Change value', '--path', str(root),
            '--key', 'cli-session-001', '--contract', str(contract),
            '--patch-file', str(patch), '--approve-writes']
    result = runner.invoke(app, cmd)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data['status'] == 'needs_real_tests'
    assert len(data['attempts']) == 1
    assert data['attempts'][0]['status'] == 'mock_passed'
    status = runner.invoke(app, ['code', 'repair-status', data['session_id']])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)['session_id'] == data['session_id']
    assert (root/'app.py').read_text() == 'VALUE = 1\n'
    replay = runner.invoke(app, cmd)
    assert replay.exit_code == 0
    assert json.loads(replay.stdout)['session_id'] == data['session_id']


def test_cli_requires_approval_even_with_mock_files(project, monkeypatch, tmp_path):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    contract = tmp_path / 'contract.json'
    contract.write_text('{"bad":true}')
    patch = tmp_path/'patch.diff';patch.write_text(PATCH)
    result = CliRunner().invoke(app,['code','repair-mock','change', '--path',str(root),
        '--key','cli-session-002','--contract',str(contract),'--patch-file',str(patch)])
    assert result.exit_code != 0
    assert 'approve-writes' in unstyle(result.output)


def test_local_api_reads_same_repair_run_with_bearer_auth(project, monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from decisionbound.web import create_app, local_token
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({
        'schema_version':'1.0', 'contract_id':'cli-fixture',
        'allowed_paths':['app.py'], 'required_paths':['app.py'],
        'test_commands':[['python','-m','pytest','-q']],
        'assertions':[{'path':'app.py','contains':'VALUE = 2','excludes':''}]
    }))
    patch = tmp_path/'patch.diff';patch.write_text(PATCH)
    result = CliRunner().invoke(app,['code','repair-mock','change', '--path',str(root),
        '--key','cli-session-003','--contract',str(contract), '--patch-file',str(patch),
        '--approve-writes'])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)['session_id']
    client = TestClient(create_app())
    assert client.get('/api/repair/'+run_id).status_code == 401
    res = client.get('/api/repair/'+run_id, headers={'Authorization': 'Bearer '+local_token()})
    assert res.status_code == 200 and res.json()['session_id'] == run_id
    assert res.json()['status'] == 'needs_real_tests'
