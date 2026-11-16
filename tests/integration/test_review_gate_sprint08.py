"""Sprint 08 local export review: end-to-end mock workflows and adversarial cases."""
from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from decisionbound.cli import app
from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.identity import init_project
from decisionbound.review_gate import (
    ExportReviewPolicy, ReviewConflict, ReviewGate, ReviewUnavailable,
)
from decisionbound.storage import SQLiteRepository
from decisionbound.supervised_coding import SupervisedCodingOrchestrator, SupervisedCodingRequest
from decisionbound.web import create_app, local_token

PATCH = ("diff --git a/app.py b/app.py\n"
         "--- a/app.py\n+++ b/app.py\n"
         "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")


@pytest.fixture
def completed(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DBOUND_HOME", str(state))
    store = SQLiteRepository(state / "state.sqlite3")
    root = tmp_path / "repository"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    for key, val in (("user.name", "Reviewer"), ("user.email", "review@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, val], check=True)
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
    init_project(root, store)
    contract = TaskContract("test_review", ("app.py",), ("app.py",),
                            (("python", "-c", "import app; assert app.VALUE == 2"),),
                            (TextPredicate("app.py", contains="VALUE = 2"),))
    workflow = SupervisedCodingOrchestrator(
        repository=store, state_root=state,
        runner=ScriptedTestRunner([0]),
        proposer_factory=ScriptedRepairFactory((PATCH,)),
    ).execute(project_path=root,
              request=SupervisedCodingRequest("Fix the value", contract, ("app.py",),
                        limits=RepairLimits(max_attempts=1)),
              idempotency_key="review_gate_s08", approved_writes=True)
    assert workflow["state"] == "blocked"
    return store, state, root, workflow["workflow_id"]


def test_review_snapshot_has_bound_evidence_but_no_source_by_default(completed):
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    preview = gate.snapshot(wid)
    assert preview["workflow_id"] == wid
    assert preview["paths"] == ["app.py"]
    assert preview["added_lines"] == preview["removed_lines"] == 1
    assert preview["contract_verified"] is False
    assert preview["export_requires_unverified_acknowledgment"]
    assert preview["approval"]["revision"] == 0
    assert preview["task_completed"] is False
    assert "diff" not in preview and "VALUE = 2" not in json.dumps(preview)
    assert gate.snapshot(wid, include_diff=True)["diff"] == PATCH
    assert (root / "app.py").read_text() == "VALUE = 1\n"


def test_explicit_approve_then_export_only_outside_checkout(completed, tmp_path):
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    preview = gate.snapshot(wid)
    fp = preview["fingerprint"]
    with pytest.raises(ReviewConflict, match="acknowledgement"):
        gate.decide(wid, expected_fingerprint=fp, expected_revision=0,
                    action="approve_export")
    decision = gate.decide(wid, expected_fingerprint=fp, expected_revision=0,
                           action="approve_export", acknowledge_unverified=True)
    assert decision["revision"] == 1
    output = tmp_path / "approved.patch"
    result = gate.export(wid, destination=output, expected_fingerprint=fp, expected_revision=1)
    assert output.read_text() == PATCH
    assert result["task_completed"] is False and result["auto_merged"] is False
    assert (root / "app.py").read_text() == "VALUE = 1\n"
    with pytest.raises(FileExistsError):
        gate.export(wid, destination=output, expected_fingerprint=fp, expected_revision=1)
    with pytest.raises(ReviewConflict, match="inside"):
        gate.export(wid, destination=root / "unsafe.patch", expected_fingerprint=fp, expected_revision=1)


def test_concurrent_approval_revision_conflict(completed):
    store, state, _, wid = completed
    preview = ReviewGate(store, state).snapshot(wid)
    def try_approve(_):
        try:
            ReviewGate(store, state).decide(wid, expected_fingerprint=preview["fingerprint"],
                                           expected_revision=0, action="approve_export",
                                           acknowledge_unverified=True)
            return "ok"
        except ReviewConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(try_approve, range(2))) == ["conflict", "ok"]


def test_revoke_prevents_export_and_rejected_snapshot_cannot_be_reapproved(completed, tmp_path):
    store, state, _, wid = completed
    gate = ReviewGate(store, state)
    fp = gate.snapshot(wid)["fingerprint"]
    gate.decide(wid, expected_fingerprint=fp, expected_revision=0,
                action="approve_export", acknowledge_unverified=True)
    revoked = gate.decide(wid, expected_fingerprint=fp, expected_revision=1, action="revoke")
    assert revoked["revision"] == 2 and revoked["decision"] == "revoke"
    with pytest.raises(ReviewConflict, match="approval"):
        gate.export(wid, destination=tmp_path / "no.patch", expected_fingerprint=fp, expected_revision=2)
    gate.decide(wid, expected_fingerprint=fp, expected_revision=2, action="reject")
    with pytest.raises(ReviewConflict, match="Rejected"):
        gate.decide(wid, expected_fingerprint=fp, expected_revision=3,
                    action="approve_export", acknowledge_unverified=True)


def test_patch_tampering_and_git_commit_change_invalidate_authorization(completed, tmp_path):
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    initial = gate.snapshot(wid)
    gate.decide(wid, expected_fingerprint=initial["fingerprint"], expected_revision=0,
                action="approve_export", acknowledge_unverified=True)
    artifact = state / "code-artifacts" / (initial["run_id"] + ".patch")
    artifact.write_text(PATCH + "# tampered\n")
    with pytest.raises(ReviewUnavailable, match="match"):
        gate.export(wid, destination=tmp_path / "bad.patch",
                    expected_fingerprint=initial["fingerprint"], expected_revision=1)
    assert not (tmp_path / "bad.patch").exists()
    artifact.write_text(PATCH)
    (root / "app.py").write_text("VALUE = 99\n")
    with pytest.raises(ReviewUnavailable, match="clean"):
        gate.snapshot(wid)


def test_symlinked_patch_and_origin_path_are_refused(completed, tmp_path):
    store, state, _, wid = completed
    gate = ReviewGate(store, state)
    snap = gate.snapshot(wid)
    artifact = state / "code-artifacts" / (snap["run_id"] + ".patch")
    # Identical bytes, so only the link check (not the digest) can refuse it.
    external = tmp_path / "external.patch"
    external.write_bytes(artifact.read_bytes())
    artifact.unlink()
    artifact.symlink_to(external)
    with pytest.raises(ReviewUnavailable, match="safely"):
        gate.snapshot(wid)


def test_veto_policy_cannot_promote_unapproved_export(completed, tmp_path):
    class Veto(ExportReviewPolicy):
        def allow(self, snapshot):
            return False
    store, state, _, wid = completed
    gate = ReviewGate(store, state, policy=Veto())
    snap = gate.snapshot(wid)
    with pytest.raises(ReviewConflict):
        gate.export(wid, destination=tmp_path / "none.patch",
                    expected_fingerprint=snap["fingerprint"], expected_revision=0)
    gate.decide(wid, expected_fingerprint=snap["fingerprint"], expected_revision=0,
                action="approve_export", acknowledge_unverified=True)
    with pytest.raises(ReviewConflict, match="veto"):
        gate.export(wid, destination=tmp_path / "none.patch",
                    expected_fingerprint=snap["fingerprint"], expected_revision=1)


def test_cli_and_api_share_reviews_but_require_explicit_confirmation(completed, tmp_path):
    _, _, root, wid = completed
    cli = CliRunner()
    result = cli.invoke(app, ["review", "inspect", wid])
    assert result.exit_code == 0, result.output
    snap = json.loads(result.output)
    token = local_token()
    client = TestClient(create_app())
    path = f"/api/workflows/{wid}/review"
    assert client.get(path).status_code == 401
    hdr = {"Authorization": "Bearer " + token}
    api = client.get(path, headers=hdr)
    assert api.status_code == 200
    assert api.json()["fingerprint"] == snap["fingerprint"]
    assert "diff" not in api.json()
    assert client.get(path, headers={**hdr, "Origin": "https://evil.example"}).status_code == 403
    assert client.post(path + "/approve_export", headers=hdr, json={
        "fingerprint": snap["fingerprint"], "revision": 0,
        "acknowledge_unverified": True}).status_code == 400
    response = client.post(path + "/approve_export", headers=hdr, json={
        "fingerprint": snap["fingerprint"], "revision": 0,
        "acknowledge_unverified": True, "confirm_export_only": True})
    assert response.status_code == 200, response.text
    out = tmp_path / "from_cli.patch"
    exported = cli.invoke(app, ["review", "export", wid, "--fingerprint", snap["fingerprint"],
                                "--revision", "1", "--output", str(out)])
    assert exported.exit_code == 0, exported.output
    assert out.read_text() == PATCH
    assert (root / "app.py").read_text() == "VALUE = 1\n"


def test_bad_fingerprint_and_id_fail_before_approval(completed):
    store, state, _, wid = completed
    gate = ReviewGate(store, state)
    assert gate.decision(wid)["decision"] == "pending"
    with pytest.raises(ReviewConflict, match="changed"):
        gate.decide(wid, expected_fingerprint="f" * 64,
                    expected_revision=0, action="approve_export", acknowledge_unverified=True)
    with pytest.raises(ReviewUnavailable):
        gate.snapshot("../../bad")
    with pytest.raises(ReviewConflict):
        gate.decide(wid, expected_fingerprint="bad", expected_revision=0, action="reject")


def test_export_and_revocation_are_serialized_under_one_db_writer_lock(completed, tmp_path, monkeypatch):
    """Revoking during export must not retroactively authorize a stale export."""
    import threading
    import time
    import decisionbound.review_gate as module
    store, state, _, wid = completed
    gate = ReviewGate(store, state)
    fp = gate.snapshot(wid)['fingerprint']
    gate.decide(wid, expected_fingerprint=fp, expected_revision=0,
                action='approve_export', acknowledge_unverified=True)
    entered = threading.Event()
    release = threading.Event()
    original = module.export_coding_patch

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Export window never released'
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'export_coding_patch', delayed)
    output = tmp_path / 'serialized.patch'
    with ThreadPoolExecutor(max_workers=2) as pool:
        export_future = pool.submit(gate.export, wid, destination=output,
                                    expected_fingerprint=fp, expected_revision=1)
        assert entered.wait(5)
        revoke_future = pool.submit(gate.decide, wid, expected_fingerprint=fp,
                                    expected_revision=1, action='revoke')
        time.sleep(.05)
        release.set()
        assert export_future.result(timeout=5)['task_completed'] is False
        assert revoke_future.result(timeout=5)['decision'] == 'revoke'
    assert output.read_text() == PATCH
    assert [e['event'] for e in gate.history(wid)] == ['approve_export', 'exported', 'revoke']
    with pytest.raises(ReviewConflict):
        gate.export(wid, destination=tmp_path / 'second.patch',
                    expected_fingerprint=fp, expected_revision=1)
