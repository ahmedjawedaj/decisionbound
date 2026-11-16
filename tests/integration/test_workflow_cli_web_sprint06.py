"""Cross-client CLI/web view of one durable offline supervisor workflow."""
from __future__ import annotations

import json
from typer.testing import CliRunner
from fastapi.testclient import TestClient

from decisionbound.cli import app
from decisionbound.identity import init_project
from decisionbound.settings import repository
from decisionbound.web import create_app, local_token


def test_cli_submit_resume_and_same_state_in_web(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path / 'state'))
    project = tmp_path / 'repo'
    project.mkdir()
    init_project(project, repository())
    manifest = tmp_path / 'workflow.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'max_workers': 2, 'tasks': [
        {'task_id':'discover', 'kind':'prepare', 'description':'Inspect approved project decisions'},
        {'task_id':'review', 'kind':'prepare', 'role':'reviewer',
         'description':'Review relevant context', 'depends_on':['discover']},
    ]}), encoding='utf-8')
    runner = CliRunner()
    assert runner.invoke(app, ['workflow','validate','--file',str(manifest)]).exit_code == 0
    created = runner.invoke(app, ['workflow','submit','--file',str(manifest),'--path',str(project)])
    assert created.exit_code == 0, created.output
    workflow_id = json.loads(created.output)['workflow_id']
    replay = runner.invoke(app, ['workflow','submit','--file',str(manifest),'--path',str(project)])
    assert json.loads(replay.output)['workflow_id'] == workflow_id
    done = runner.invoke(app, ['workflow','run',workflow_id])
    assert done.exit_code == 0, done.output
    assert json.loads(done.output)['state'] == 'verified_stages'
    checked = runner.invoke(app, ['workflow','status',workflow_id])
    assert json.loads(checked.output)['task_completed'] is False
    event_out = runner.invoke(app, ['workflow','events',workflow_id])
    assert json.loads(event_out.output)[-1]['event'] == 'finished'
    client = TestClient(create_app())
    bearer = {'Authorization': 'Bearer ' + local_token()}
    assert client.get('/api/workflows/' + workflow_id).status_code == 401
    api = client.get('/api/workflows/' + workflow_id, headers=bearer)
    assert api.status_code == 200
    assert api.json() == json.loads(checked.output)
    events = client.get('/api/workflows/' + workflow_id + '/events', headers=bearer)
    assert events.status_code == 200
    assert events.json()[-1]['event'] == 'finished'
    assert client.post('/api/workflows/' + workflow_id + '/reconcile', headers=bearer).json()['state'] == 'verified_stages'
    assert client.get('/api/workflows/missing', headers=bearer).status_code == 404


def test_workflow_cli_cannot_run_code_without_worker_approval(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path/'state'))
    project = tmp_path/'repository'
    project.mkdir()
    init_project(project, repository())
    manifest = tmp_path/'code.yaml'
    manifest.write_text('schema_version: 1\ntasks:\n  - task_id: write\n    kind: code\n    description: Change repository code\n')
    runner = CliRunner()
    created = runner.invoke(app, ['workflow','submit','--file',str(manifest),'--path',str(project)])
    assert created.exit_code == 0, created.output
    workflow_id = json.loads(created.output)['workflow_id']
    result = runner.invoke(app, ['workflow','run',workflow_id])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['state'] == 'running'
    assert json.loads(result.output)['tasks'][0]['state'] == 'pending'


def test_workflow_cli_rejects_malformed_manifest_and_large_file(tmp_path):
    bad = tmp_path/'unsafe.yaml'
    bad.write_text('schema_version: 1\ntasks: !!python/object/apply:os.system ["echo hello"]')
    runner = CliRunner()
    assert runner.invoke(app, ['workflow','validate','--file',str(bad)]).exit_code != 0
    bad.write_bytes(b'x'*64001)
    assert runner.invoke(app, ['workflow','validate','--file',str(bad)]).exit_code != 0


def test_local_ui_has_inspector_without_inline_secret_exposure(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path / 'private'))
    client = TestClient(create_app())
    page = client.get('/')
    assert page.status_code == 200
    assert 'Inspect workflow' in page.text
    assert '/api/workflows/' in page.text
    assert local_token() not in page.text
    assert 'task.state' in page.text
