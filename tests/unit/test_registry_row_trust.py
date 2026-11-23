"""Orphan reaper: a registry row is never trusted beyond what the host can check.

Two corrections are covered. A row with no recorded start token (the identity
could not be read at spawn) used to be called ``identity_mismatch`` once the
process id was alive, which does not block the task. It is now ``unconfirmed``,
nothing is signalled and the task is blocked. On the Windows path a row that
names the host's own process id, or an id below 2, is refused before any
``taskkill``, as the POSIX path already did.

Real processes are used for the POSIX cases. The Windows case uses fakes, so it
shows the decision logic only and says nothing about a real Windows host.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from decisionbound import containment as C
from decisionbound.storage import SQLiteRepository

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")


class Journal:
    def __init__(self):
        self.events, self.blocked = [], []

    def record_containment_event(self, workflow_id, task_id, event, detail):
        self.events.append(event)

    def block_uncontained(self, workflow_id, task_id, generation):
        self.blocked.append(task_id)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def registry(tmp_path):
    return C.ProcessRegistry(SQLiteRepository(tmp_path / "state.sqlite3"))


@pytest.fixture
def bystander():
    proc = subprocess.Popen([sys.executable, "-c", "import time\ntime.sleep(600)"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    yield proc
    proc.kill()
    proc.wait()


def orphan_row(registry, pid, identity, *, level="posix_session"):
    """Write a row whose owner is long dead, the way a crashed coordinator leaves it."""
    cid = registry.record_intent(workflow_id="wf_x", task_id="stage", attempt=1, generation=1, level=level,
                                 argv_sha256="0" * 64, owner=C.ProcessIdentity(2 ** 22 + 1, "dead-owner"))
    registry.record_started(cid, pid=pid, pgid=pid, identity=identity)
    return cid


def reap(registry):
    journal = Journal()
    report = C.OrphanReaper(registry, journal, term_grace_seconds=0.5).reap(leader_generation=1)
    return report, journal


@posix_only
def test_live_process_with_no_recorded_identity_is_unconfirmed_and_blocks(registry, bystander):
    cid = orphan_row(registry, bystander.pid, None)  # identity unreadable at spawn
    report, journal = reap(registry)
    assert alive(bystander.pid), "a process that may be the worker was signalled"
    assert report["unconfirmed"] == 1 and report["identity_mismatch"] == 0
    assert journal.blocked == ["stage"] and journal.events == ["orphan_unconfirmed"]
    with registry.store._connection() as db:
        assert db.execute("SELECT state FROM contained_processes WHERE containment_id=?", (cid,)).fetchone()[0] == "unconfirmed"


@posix_only
@pytest.mark.parametrize("empty", ["", None])
def test_empty_and_null_start_tokens_are_both_unconfirmed(registry, bystander, empty):
    cid = orphan_row(registry, bystander.pid, C.ProcessIdentity(bystander.pid, "x"))
    with registry.store._connection() as db:
        db.execute("UPDATE contained_processes SET start_token=? WHERE containment_id=?", (empty, cid))
    report, journal = reap(registry)
    assert alive(bystander.pid)
    assert report["unconfirmed"] == 1 and journal.blocked == ["stage"]


@posix_only
def test_a_wrong_but_present_start_token_is_still_a_plain_mismatch(registry, bystander):
    orphan_row(registry, bystander.pid, C.ProcessIdentity(bystander.pid, "not-the-real-token"))
    report, journal = reap(registry)
    assert alive(bystander.pid)
    assert report["identity_mismatch"] == 1 and report["unconfirmed"] == 0 and journal.blocked == []


@posix_only
def test_a_row_with_no_identity_and_nothing_alive_is_gone(registry):
    corpse = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    corpse.wait()
    orphan_row(registry, corpse.pid, None)
    report, journal = reap(registry)
    assert report["gone"] == 1 and journal.blocked == []


@pytest.fixture
def windows(monkeypatch):
    state = SimpleNamespace(live={}, taskkills=[])
    monkeypatch.setattr(C, "_IS_NT", True)
    monkeypatch.setattr(C, "process_identity", lambda pid: state.live.get(pid))
    monkeypatch.setattr(C._WindowsJob, "open_existing", staticmethod(lambda name: None), raising=False)
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: state.taskkills.append(argv))
    monkeypatch.setattr(C, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(C, "_CONFIRM_SECONDS", 0.05)
    return state


class OneRow:
    def __init__(self, row):
        self.rows, self.marked = [row], {}

    def running(self):
        return self.rows

    def mark(self, containment_id, state):
        self.marked[containment_id] = state


def windows_row(pid, token):
    return {"containment_id": "cp_" + "ab" * 16, "workflow_id": "wf_x", "task_id": "stage", "generation": 1,
            "owner_pid": 1111, "owner_start_token": "owner", "leader_generation": 1,
            "level": "windows_tree_best_effort", "pid": pid, "pgid": pid, "start_token": token}


@pytest.mark.parametrize("pid", [os.getpid(), 1, 0, -5])
def test_windows_path_never_taskkills_the_host_or_a_low_pid(windows, pid):
    windows.live[pid] = C.ProcessIdentity(pid, "win:1")  # a forged row can copy a live token
    registry = OneRow(windows_row(pid, "win:1"))
    report = C.OrphanReaper(registry, Journal(), term_grace_seconds=0.0).reap(leader_generation=2)
    assert windows.taskkills == []
    assert report["identity_mismatch"] == 1


@pytest.mark.parametrize("pid", ["4242", 4242.0, True])
def test_windows_path_refuses_a_pid_that_is_not_a_plain_integer(windows, pid):
    """A hand-edited row can hold text or a float that a lookup or taskkill would still accept."""
    windows.live[pid] = C.ProcessIdentity(4242, "win:1")
    registry = OneRow(windows_row(pid, "win:1"))
    report = C.OrphanReaper(registry, Journal(), term_grace_seconds=0.0).reap(leader_generation=2)
    assert windows.taskkills == []
    assert report["identity_mismatch"] == 1


def test_windows_path_live_process_with_no_recorded_identity_is_unconfirmed(windows):
    windows.live[4242] = C.ProcessIdentity(4242, "win:1")
    registry, journal = OneRow(windows_row(4242, "")), Journal()
    report = C.OrphanReaper(registry, journal, term_grace_seconds=0.0).reap(leader_generation=2)
    assert windows.taskkills == []
    assert report["unconfirmed"] == 1 and journal.blocked == ["stage"]
