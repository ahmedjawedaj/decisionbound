"""Cross-client contract: CLI and web share a single local runtime registry."""
import os
from pathlib import Path
from typer.testing import CliRunner
import pytest
from decisionbound.cli import app
from decisionbound.web import create_app, local_token
from fastapi.testclient import TestClient


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_cli_end_to_end_smoke(environment):
    runner = CliRunner()
    for args in (["init", "--path", str(environment), "--technologies", "python"],
                 ["status", "--path", str(environment)],
                 ["profile", "create", "my-team", "--from", "codex"],
                 ["profile", "use", "my-team"],
                 ["prepare", "Implement a read-only API", "--path", str(environment)],
                 ["run", "Implement a read-only API", "--path", str(environment)]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.output, result.exception)
    assert '"task_completed": false' in result.output.lower()


def test_api_rejects_missing_token(environment):
    client = TestClient(create_app())
    assert client.get("/api/profiles").status_code == 401


def test_cli_web_profile_sync(environment):
    runner = CliRunner()
    assert runner.invoke(app, ["profile", "use", "claude"]).exit_code == 0
    client = TestClient(create_app())
    token = local_token()
    result = client.get("/api/profiles", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    assert result.json()["active"] == "claude"
    response = client.post("/api/profiles/local-safe/activate", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert '"active": "local-safe"' in runner.invoke(app, ["profile", "list"]).output
