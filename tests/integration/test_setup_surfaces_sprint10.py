"""One setup state across the CLI and the authenticated local API."""
import json
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from decisionbound.cli import app
from decisionbound.web import create_app, local_token

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(tmp_path / 'home'))
    repo = tmp_path / 'project'; repo.mkdir()
    return repo


def test_first_run_cli_then_same_status_in_http(env):
    runner = CliRunner()
    configured = runner.invoke(app, ['setup', '--profile', 'local-safe', '--path', str(env), '--initialize'])
    assert configured.exit_code == 0, (configured.output, configured.exception)
    setup = json.loads(configured.output)
    assert setup['ready'] and setup['project_registered']
    api = TestClient(create_app())
    assert api.get('/api/setup/v1/status').status_code == 401
    http = api.get('/api/setup/v1/status', headers={'Authorization': 'Bearer ' + local_token()})
    assert http.status_code == 200
    assert http.json()['profile'] == 'local-safe' and http.json()['ready']


def test_strict_doctor_checks_missing_required_tool_and_does_not_change_profile(env, monkeypatch):
    # Exercise the missing-tool branch regardless of software on the test host.
    monkeypatch.setattr('decisionbound.onboarding.shutil.which', lambda command: None)
    runner = CliRunner()
    result = runner.invoke(app, ['doctor', '--profile', 'local', '--strict'])
    assert result.exit_code == 2, (result.output, result.exception)
    assert json.loads(result.output)['profile'] == 'local'
    assert any(check['key'] == 'ollama_cli' and not check['ok']
               for check in json.loads(result.output)['checks'])
    assert json.loads(runner.invoke(app, ['profile', 'list']).output)['active'] == 'local-safe'


def test_setup_invalid_profile_fails_closed(env):
    result = CliRunner().invoke(app, ['setup', '--profile', 'nonsense', '--path', str(env), '--initialize'])
    assert result.exit_code != 0
    assert not (env / '.decisionbound').exists()


def test_no_key_or_token_in_http_response(env, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'never-display-this-value-123')
    runner = CliRunner()
    assert runner.invoke(app, ['setup', '--profile', 'openrouter']).exit_code == 0
    api = TestClient(create_app())
    response = api.get('/api/setup/v1/status', headers={'Authorization': 'Bearer ' + local_token()})
    assert response.status_code == 200
    assert 'never-display' not in response.text
    assert local_token() not in response.text
    assert response.json()['integration_tested'] is False
