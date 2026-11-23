"""A Git failure during review must refuse cleanly, never 500, leak, or approve."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from decisionbound.mcp_server import create_mcp_server
from decisionbound.review_gate import ReviewGate, ReviewGitUnavailable, ReviewUnavailable
from decisionbound.web import create_app, local_token
from tests.contract.test_mcp_server_contract import FakeMCPServer
from tests.integration.test_review_gate_sprint08 import completed  # noqa: F401

REFUSAL = "Git state could not be verified, so the review was refused"
LEAK_MARKER = "STDERR-LEAK-MARKER"
SHIM_KINDS = ("mode-check-exit", "filter-check-exit", "filter-check-timeout", "status-exit")
needs_posix_shell = pytest.mark.skipif(
    sys.platform == "win32", reason="the Git shim is a POSIX shell script and Windows runs the real git.exe")


def _install_shim(tmp_path, monkeypatch, fail_on: str, how: str) -> None:
    """Put a real-Git wrapper first on PATH that breaks one Git subcommand."""
    real = shutil.which("git")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    if how == "exit":
        action = f'echo "{LEAK_MARKER} $PWD" >&2\n    exit 128'
    else:
        action = "exec sleep 30"
    script = (
        "#!/bin/sh\n"
        f'for arg in "$@"\ndo\n  if [ "$arg" = "{fail_on}" ]\n  then\n    {action}\n  fi\ndone\n'
        f'exec "{real}" "$@"\n'
    )
    shim = shim_dir / "git"
    shim.write_text(script)
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ["PATH"])
    if how == "hang":
        run = subprocess.run
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: run(*a, **{**k, "timeout": 1}))


def _break_git(kind: str, tmp_path, monkeypatch, root) -> None:
    if kind == "mode-check-exit":
        _install_shim(tmp_path, monkeypatch, "ls-files", "exit")
    elif kind == "filter-check-exit":
        _install_shim(tmp_path, monkeypatch, "--get-regexp", "exit")
    elif kind == "filter-check-timeout":
        _install_shim(tmp_path, monkeypatch, "--name-only", "hang")
    elif kind == "status-exit":
        _install_shim(tmp_path, monkeypatch, "status", "exit")
    elif kind == "git-missing":
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    elif kind == "corrupt-index":
        (root / ".git" / "index").write_bytes(b"not an index")
    else:
        raise AssertionError(kind)


KINDS = [pytest.param(kind, marks=needs_posix_shell) if kind in SHIM_KINDS else kind
         for kind in ("mode-check-exit", "filter-check-exit", "filter-check-timeout", "status-exit",
                      "git-missing", "corrupt-index")]


def _assert_clean(text: str, tmp_path) -> None:
    assert str(tmp_path) not in text and LEAK_MARKER not in text
    assert "CalledProcessError" not in text and "Command '" not in text


@pytest.mark.parametrize("kind", KINDS)
def test_snapshot_refuses_with_typed_bounded_error(completed, tmp_path, monkeypatch, kind):  # noqa: F811
    store, state, root, wid = completed
    _break_git(kind, tmp_path, monkeypatch, root)
    with pytest.raises(ReviewGitUnavailable) as caught:
        ReviewGate(store, state).snapshot(wid)
    assert isinstance(caught.value, ReviewUnavailable)
    assert str(caught.value) == REFUSAL
    _assert_clean(str(caught.value), tmp_path)


@pytest.mark.parametrize("kind", KINDS)
def test_approval_fails_closed_and_records_nothing(completed, tmp_path, monkeypatch, kind):  # noqa: F811
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    fingerprint = gate.snapshot(wid)["fingerprint"]
    _break_git(kind, tmp_path, monkeypatch, root)
    with pytest.raises(ReviewGitUnavailable):
        gate.decide(wid, expected_fingerprint=fingerprint, expected_revision=0,
                    action="approve_export", acknowledge_unverified=True)
    assert gate.decision(wid)["decision"] == "pending"
    assert gate.history(wid) == []


@pytest.mark.parametrize("kind", KINDS)
def test_export_fails_closed_and_writes_no_file(completed, tmp_path, monkeypatch, kind):  # noqa: F811
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    fingerprint = gate.snapshot(wid)["fingerprint"]
    gate.decide(wid, expected_fingerprint=fingerprint, expected_revision=0,
                action="approve_export", acknowledge_unverified=True)
    _break_git(kind, tmp_path, monkeypatch, root)
    output = tmp_path / "out.patch"
    with pytest.raises(ReviewGitUnavailable):
        gate.export(wid, destination=output, expected_fingerprint=fingerprint, expected_revision=1)
    assert not output.exists()
    assert [event["event"] for event in gate.history(wid)] == ["approve_export"]


@pytest.mark.parametrize("kind", KINDS)
def test_http_review_routes_return_stable_409_without_leaks(completed, tmp_path, monkeypatch, kind):  # noqa: F811
    store, state, root, wid = completed
    fingerprint = ReviewGate(store, state).snapshot(wid)["fingerprint"]
    headers = {"Authorization": "Bearer " + local_token()}
    client = TestClient(create_app(), raise_server_exceptions=False)
    _break_git(kind, tmp_path, monkeypatch, root)
    path = f"/api/workflows/{wid}/review"
    view = client.get(path, headers=headers)
    approve = client.post(path + "/approve_export", headers=headers, json={
        "fingerprint": fingerprint, "revision": 0,
        "acknowledge_unverified": True, "confirm_export_only": True})
    for response in (view, approve):
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": REFUSAL}
        _assert_clean(response.text, tmp_path)
    assert ReviewGate(store, state).decision(wid)["decision"] == "pending"


@needs_posix_shell
def test_mcp_review_metadata_refuses_with_bounded_error(completed, tmp_path, monkeypatch):  # noqa: F811
    store, _, root, wid = completed
    server = create_mcp_server(root, store, FakeMCPServer)
    _break_git("mode-check-exit", tmp_path, monkeypatch, root)
    with pytest.raises(ReviewGitUnavailable) as caught:
        server.tools["get_workflow_review_metadata"](wid)
    assert str(caught.value) == REFUSAL
    _assert_clean(str(caught.value), tmp_path)


def test_dirty_checkout_keeps_its_own_refusal(completed):  # noqa: F811
    store, state, root, wid = completed
    (root / "app.py").write_text("VALUE = 9\n")
    with pytest.raises(ReviewUnavailable, match="missing or not clean") as caught:
        ReviewGate(store, state).snapshot(wid)
    assert not isinstance(caught.value, ReviewGitUnavailable)


def test_unsafe_tracked_mode_is_a_scope_refusal_not_a_git_failure(completed, monkeypatch):  # noqa: F811
    from decisionbound.code_patches import PatchValidator, UnsafePatch
    store, state, _, wid = completed

    def unsafe(*args, **kwargs):
        raise UnsafePatch("Tracked target is not a regular non-executable file")

    monkeypatch.setattr(PatchValidator, "check_git_modes", unsafe)
    with pytest.raises(ReviewUnavailable, match="independent scope validation") as caught:
        ReviewGate(store, state).snapshot(wid)
    assert not isinstance(caught.value, ReviewGitUnavailable)


@needs_posix_shell
def test_source_check_reports_a_hung_filter_lookup_as_a_typed_git_error(completed, tmp_path, monkeypatch):  # noqa: F811
    from decisionbound.code_workspace import GitCommandError, GitWorktreeManager
    _, _, root, _ = completed
    _break_git("filter-check-timeout", tmp_path, monkeypatch, root)
    with pytest.raises(GitCommandError) as caught:
        GitWorktreeManager.check_source(root)
    _assert_clean(str(caught.value), tmp_path)


@needs_posix_shell
def test_source_check_reports_a_failed_filter_lookup_as_a_typed_git_error(completed, tmp_path, monkeypatch):  # noqa: F811
    from decisionbound.code_workspace import GitCommandError, GitWorktreeManager
    _, _, root, _ = completed
    _break_git("filter-check-exit", tmp_path, monkeypatch, root)
    with pytest.raises(GitCommandError) as caught:
        GitWorktreeManager.check_source(root)
    _assert_clean(str(caught.value), tmp_path)


@needs_posix_shell
def test_revoke_does_not_need_git_and_still_works_when_git_is_down(completed, tmp_path, monkeypatch):  # noqa: F811
    store, state, root, wid = completed
    gate = ReviewGate(store, state)
    fingerprint = gate.snapshot(wid)["fingerprint"]
    gate.decide(wid, expected_fingerprint=fingerprint, expected_revision=0,
                action="approve_export", acknowledge_unverified=True)
    _break_git("status-exit", tmp_path, monkeypatch, root)
    revoked = gate.decide(wid, expected_fingerprint=fingerprint, expected_revision=1, action="revoke")
    assert revoked["decision"] == "revoke"
    assert [event["event"] for event in gate.history(wid)] == ["approve_export", "revoke"]
