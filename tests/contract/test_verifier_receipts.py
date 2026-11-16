"""Independent receipt validation: worker claims alone are insufficient."""
import hashlib
import json
from decisionbound.execution import ReadOnlyEvidenceVerifier, ReadOnlyLedgerVerifier
from decisionbound.models import WorkerResult
from decisionbound.storage import SQLiteRepository


def test_ledger_evidence_requires_host_receipts(tmp_path):
    state = SQLiteRepository(tmp_path / "state.sqlite3")
    observation = json.dumps({"tool": "read_file", "result": {"path": "safe.py"}})
    result = WorkerResult("prepared", "Done", (observation,))
    assert ReadOnlyEvidenceVerifier().verify("Inspect safe.py", result)
    verifier = ReadOnlyLedgerVerifier(state, "run_1")
    assert verifier.verify("Inspect safe.py", result) is False
    state.record_event("run_1", "tool_observation", {"name": "read_file", "size": len(observation),
        "sha256": hashlib.sha256(observation.encode()).hexdigest(), "truncated": False})
    assert verifier.verify("Inspect safe.py", result) is True
    assert verifier.verify("Inspect safe.py", WorkerResult("prepared", "Done", (observation+' ',))) is False


def test_truncated_tool_output_cannot_be_independently_verified(tmp_path):
    state = SQLiteRepository(tmp_path / "db.sqlite3")
    observation = json.dumps({"tool": "search_project", "result": {"results": []}})
    state.record_event("run_1", "tool_observation", {"size": len(observation),
        "sha256": hashlib.sha256(observation.encode()).hexdigest(), "truncated": True})
    assert not ReadOnlyLedgerVerifier(state, "run_1").verify("query", WorkerResult("prepared", "ok", (observation,)))
