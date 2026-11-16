"""Reproducible no-credential repo-inspection demo for DecisionBound 0.3.

Run: python examples/mock_readonly_agent.py

This creates a temporary repository, executes a deterministic model-shaped
planner and verifies that tool trace receipts cannot claim task completion.
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from decisionbound.execution import run_readonly_agent
from decisionbound.identity import init_project
from decisionbound.model_planner import Pricing, ScriptedModelTransport
from decisionbound.storage import SQLiteRepository


def main() -> None:
    """Create an ephemeral reproducible demonstration without external services."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "project"
        root.mkdir()
        (root / "payments.py").write_text("def authorize_refund(): return False\n")
        state = SQLiteRepository(Path(directory) / "state.sqlite3")
        init_project(root, state, technologies=("python",))
        outputs = [
            json.dumps({"kind": "tool", "tool": "search_project",
                        "arguments_json": '{"query":"authorize_refund"}', "message": ""}),
            json.dumps({"kind": "finish", "tool": None, "arguments_json": "{}",
                        "message": "Found a relevant source file. Completion unverified."}),
        ]
        result = run_readonly_agent(project_path=root, store=state, task="Find refund authorization",
                                    transport=ScriptedModelTransport(outputs), pricing=Pricing(0, 0))
        print(json.dumps({"run_id": result["run_id"], "task_completed": result["task_completed"],
                          "verified_tool_evidence": result["evidence_inspection_verified"],
                          "tool_calls": len(result["result"]["observations"]),
                          "events": [e["event"] for e in state.events(result["run_id"])]}, indent=2))


if __name__ == "__main__":
    main()
