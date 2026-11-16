"""End-to-end mocked model->tool->trace->verifier loops (no paid APIs)."""
import json
from pathlib import Path
import pytest
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.execution import run_readonly_agent, ReadOnlyEvidenceVerifier
from decisionbound.model_planner import (
    ScriptedModelTransport, Pricing, ModelLimits, ModelBudgetExceeded, ModelOutputError)
from decisionbound.models import WorkerResult
from decisionbound.tools import ToolPermissionDenied


def action(tool, args):
    return json.dumps({"kind": "tool", "tool": tool, "arguments_json": json.dumps(args), "message": ""})


def finish(msg="Inspection finished; not verified task completion"):
    return json.dumps({"kind": "finish", "tool": None, "arguments_json": "{}", "message": msg})


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "service.py").write_text('def verify_payment():\n    return True\n')
    (root / ".env").write_text("PRIVATE_TOKEN=visible_if_broken")
    store = SQLiteRepository(tmp_path / "state" / "db.sqlite3")
    init_project(root, store, technologies=("python",))
    return root, store


def test_two_tool_steps_evidence_verification_and_trace_redaction(context):
    root, store = context
    outputs = [action("search_project", {"query": "verify_payment"}),
               action("read_file", {"path": "service.py"}), finish()]
    result = run_readonly_agent(project_path=root, store=store, task="Find verify_payment",
                                transport=ScriptedModelTransport(outputs), pricing=Pricing(0, 0),
                                limits=ModelLimits(max_calls=4))
    assert result["preparation_verified"] is True
    assert result["evidence_inspection_verified"] is True
    assert result["task_completed"] is False
    assert len(result["result"]["observations"]) == 2
    events = store.events(result["run_id"])
    assert len([e for e in events if e["event"] == "tool_invocation"]) == 2
    assert len([e for e in events if e["event"] == "model_request"]) == 3
    assert result["model_accounting"]["calls"] == 3
    assert "verify_payment" not in json.dumps(events) or "service.py" not in json.dumps(events)
    assert "PRIVATE_TOKEN" not in json.dumps(events)


def test_empty_agent_finish_is_never_evidence_verified(context):
    root, store = context
    result = run_readonly_agent(project_path=root, store=store, task="Analyze code",
                                transport=ScriptedModelTransport([finish("Done!")]), pricing=Pricing(0, 0))
    assert result["task_completed"] is False
    assert result["preparation_verified"] is False
    assert ReadOnlyEvidenceVerifier().verify("task", WorkerResult("completed", "yes", ())) is False


def test_run_rejects_secret_reads_and_traces_failure(context):
    root, store = context
    with pytest.raises(PermissionError):
        run_readonly_agent(project_path=root, store=store, task="Reveal credentials",
            transport=ScriptedModelTransport([action("read_file", {"path": ".env"})]),
            pricing=Pricing(0, 0))
    assert "PRIVATE_TOKEN" not in json.dumps(store.events("any"))


def test_offered_tool_restriction(context):
    root, store = context
    with pytest.raises(ModelOutputError):
        run_readonly_agent(project_path=root, store=store, task="Read code",
            transport=ScriptedModelTransport([action("read_file", {"path": "service.py"})]),
            allowed_tools={"search_project"}, pricing=Pricing(0, 0))
    with pytest.raises(PermissionError):
        run_readonly_agent(project_path=root, store=store, task="Read code",
            transport=ScriptedModelTransport([finish()]),
            allowed_tools={"delete_everything"}, pricing=Pricing(0, 0))


def test_budget_prevents_more_requests_after_exhaustion(context):
    root, store = context
    fake = ScriptedModelTransport([action("search_project", {"query": "service"}), finish()])
    with pytest.raises(RuntimeError, match="budget"):
        run_readonly_agent(project_path=root, store=store, task="Inspect service",
            transport=fake, pricing=Pricing(0, 0), limits=ModelLimits(max_calls=1))
    assert len(fake.requests) == 1


def test_wrong_project_denied(context, tmp_path):
    root, store = context
    another = tmp_path / "another"
    another.mkdir()
    with pytest.raises(FileNotFoundError):
        run_readonly_agent(project_path=another, store=store, task="Inspect",
            transport=ScriptedModelTransport([finish()]), pricing=Pricing(0, 0))


def test_failed_execution_provides_recoverable_event_id(context):
    """A failure must expose a safe event ID without swallowing its exception."""
    root, store = context
    with pytest.raises(ModelOutputError) as captured:
        run_readonly_agent(project_path=root, store=store, task="Inspect project",
            transport=ScriptedModelTransport(["garbled model output"]), pricing=Pricing(0, 0))
    notes = getattr(captured.value, "__notes__", [])
    assert notes and "run_id=" in notes[0]
    run_id = notes[0].split("run_id=")[1].split(";")[0]
    events = store.events(run_id)
    assert [e["event"] for e in events][-1] == "failed"
    assert events[-1]["payload"] == {"error_type": "ModelOutputError"}
