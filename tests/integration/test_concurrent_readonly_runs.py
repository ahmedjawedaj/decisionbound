"""Concurrent, mock-only durability and event-isolation stress test."""
import json
from concurrent.futures import ThreadPoolExecutor
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.execution import run_readonly_agent
from decisionbound.model_planner import ScriptedModelTransport, Pricing, ModelLimits


def test_concurrent_mock_runs_do_not_mix_receipts(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "module.py").write_text("def sample_fixture(): pass\n")
    state = SQLiteRepository(tmp_path / "state.sqlite3")
    init_project(root, state)

    def execute(i):
        request = json.dumps({"kind": "tool", "tool": "search_project",
                              "arguments_json": '{"query":"sample_fixture"}', "message": ""})
        conclusion = json.dumps({"kind": "finish", "tool": None, "arguments_json": "{}",
                                 "message": "Inspected (not user-task-complete)"})
        return run_readonly_agent(project_path=root, store=state, task=f"Inspect fixture {i}",
            transport=ScriptedModelTransport([request, conclusion]), pricing=Pricing(0, 0),
            limits=ModelLimits(max_calls=3))

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(execute, range(24)))
    assert len({r["run_id"] for r in receipts}) == 24
    assert all(r["evidence_inspection_verified"] and not r["task_completed"] for r in receipts)
    for receipt in receipts:
        events = state.events(receipt["run_id"])
        assert events[0]["event"] == "started"
        assert events[-1]["event"] == "verification"
        assert len([e for e in events if e["event"] == "tool_observation"]) == 1
        assert len([e for e in events if e["event"] == "model_request"]) == 2
        assert "sample_fixture" not in json.dumps(events)
