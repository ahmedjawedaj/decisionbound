"""The killable preparation child: real processes, real state file, no fakes.

The child is started the way production starts it, with ``python -I -m``, so
these tests need ``decisionbound`` installed (CI installs it). Nothing here
patches the process layer.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from decisionbound import worker_child as child
from decisionbound.containment import ProcessRegistry, WorkerCrashed
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import (
    PreparationWorker, TaskAssignment, WorkflowJournal, WorkflowSpec, WorkflowSupervisor, WorkflowTask,
)


@pytest.fixture
def env(tmp_path):
    store = SQLiteRepository(tmp_path / "private" / "state.sqlite3")
    home = tmp_path / "repo"
    home.mkdir()
    project = init_project(home, store)
    return store, home, project, WorkflowJournal(store), ProcessRegistry(store)


def _payload(home, project, kind="prepare", **overrides):
    value = {"workflow_id": "wf_test", "project_id": project.project_id, "project_path": str(home),
             "task": {"task_id": "one", "kind": kind, "description": "Inspect repository conventions"},
             "attempt": 1, "reserved_cost_usd": 0.0, "handoffs": []}
    value.update(overrides)
    return value


def _run_child(store, stdin: bytes):
    return subprocess.run([sys.executable, "-I", "-m", "decisionbound.worker_child",
                           "--database", str(store.path)], input=stdin, capture_output=True, timeout=60)


def test_child_receipt_matches_the_in_process_worker(env):
    store, home, project, *_ = env
    done = _run_child(store, json.dumps(_payload(home, project)).encode())
    assert done.returncode == 0, done.stderr
    lines = done.stdout.decode().splitlines()
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assignment = child._assignment(json.dumps(_payload(home, project)).encode())
    expected = PreparationWorker(store).execute(assignment)
    assert receipt == {"status": "prepared", "evidence_sha256": expected.evidence_sha256}
    assert len(receipt["evidence_sha256"]) == 64


@pytest.mark.parametrize("stdin", [
    b"", b"not json", b"[]", b'{"workflow_id": "x"}',
    b"x" * (child.MAX_ASSIGNMENT_BYTES + 10),
])
def test_child_prints_nothing_for_malformed_input(env, stdin):
    store, *_ = env
    done = _run_child(store, stdin)
    assert done.returncode != 0
    assert done.stdout == b""


def test_child_rejects_an_unknown_assignment_field(env):
    store, home, project, *_ = env
    done = _run_child(store, json.dumps(_payload(home, project, owner_token="secret")).encode())
    assert done.returncode != 0 and done.stdout == b""


@pytest.mark.parametrize("kind", ["code", "inspect", "review"])
def test_child_refuses_every_kind_except_prepare(env, kind):
    store, home, project, *_ = env
    done = _run_child(store, json.dumps(_payload(home, project, kind=kind)).encode())
    assert done.returncode != 0 and done.stdout == b""


def test_child_rejects_a_project_identity_that_does_not_match_the_checkout(env):
    store, home, project, *_ = env
    done = _run_child(store, json.dumps(_payload(home, project, project_id="prj_wrong_identity")).encode())
    assert done.returncode != 0 and done.stdout == b""


def test_supervisor_verifies_a_contained_preparation(env):
    store, home, project, journal, registry = env
    worker = child.contained_preparation_worker(registry, deadline_seconds=60)
    spec = WorkflowSpec((WorkflowTask("one", "prepare", "Inspect repository conventions"),), 1, 0)
    wid = journal.create(project_path=home, spec=spec, idempotency_key="contained_prepare")
    result = WorkflowSupervisor(journal, workers={"prepare": worker}).run(wid, lease_seconds=60, max_wall_seconds=120)
    assert result["state"] == "verified_stages"
    assert result["task_completed"] is False
    rows = registry.store
    with rows._connection() as db:
        states = [r["state"] for r in db.execute("SELECT state FROM contained_processes")]
    assert states and all(s not in ("spawning", "running") for s in states), states


def test_a_failing_child_is_a_crash_not_a_receipt(env):
    store, home, project, journal, registry = env
    worker = child.contained_preparation_worker(registry, deadline_seconds=60)
    assignment = TaskAssignment("wf_test", "prj_wrong_identity", home,
                                WorkflowTask("one", "prepare", "Inspect repository conventions"), 1, 0.0)
    with pytest.raises(WorkerCrashed):
        worker.execute(assignment)


def test_cli_serve_contained_verifies_a_workflow_in_a_child_process(env, tmp_path):
    import os
    store, home, project, journal, registry = env
    spec = WorkflowSpec((WorkflowTask("one", "prepare", "Inspect repository conventions"),), 1, 0)
    wid = journal.create(project_path=home, spec=spec, idempotency_key="cli_contained")
    environment = dict(os.environ, DBOUND_HOME=str(store.path.parent))
    done = subprocess.run([sys.executable, "-m", "decisionbound.cli", "workflow", "serve", "--once",
                           "--contained", "--path", str(home)], env=environment,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert journal.status(wid)["state"] == "verified_stages"
    with store._connection() as db:
        spawned = db.execute("SELECT COUNT(*) FROM contained_processes").fetchone()[0]
    assert spawned == 1, "the task must have run in a registered child process"
