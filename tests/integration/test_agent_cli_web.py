"""CLI and HTTP clients observe the same audited demo agent results."""
import json
from click import unstyle
from typer.testing import CliRunner
from fastapi.testclient import TestClient
from decisionbound.cli import app
from decisionbound.web import create_app, local_token


def test_cli_demo_and_web_run_share_event_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "entry.py").write_text("def target_function(): pass\n")
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--path", str(root), "--technologies", "python"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["agent", "demo", "target_function", "--path", str(root)])
    assert result.exit_code == 0, (result.output, result.exception)
    output = json.loads(result.output)
    assert output["task_completed"] is False
    assert output["evidence_inspection_verified"] is True
    assert "entry.py" in str(output["result"]["observations"])
    assert output["model_accounting"]["calls"] == 2
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {local_token()}"}
    events = client.get(f"/api/runs/{output['run_id']}/events", headers=headers)
    assert events.status_code == 200
    assert any(e["event"] == "model_response" for e in events.json())
    http_run = client.post("/api/agent/demo", headers=headers,
        json={"project_path": str(root), "task": "target_function", "max_tokens": 1000})
    assert http_run.status_code == 200, http_run.text
    assert http_run.json()["task_completed"] is False
    assert http_run.json()["evidence_inspection_verified"] is True


def test_agent_cli_remote_denied_without_user_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"
    root.mkdir()
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--path", str(root)]).exit_code == 0
    result = runner.invoke(app, ["agent", "run", "Inspect code", "--path", str(root),
                                 "--model", "mock", "--input-price", "1", "--output-price", "1"])
    assert result.exit_code != 0
    assert "--allow-remote" in unstyle(result.output)


def test_api_agent_demo_requires_local_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "state"))
    client = TestClient(create_app())
    assert client.post("/api/agent/demo", json={"project_path": str(tmp_path), "task": "demo"}).status_code == 401
