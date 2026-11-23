"""Windows orphan reaper: the job object check, tested with fakes so it runs on every OS.

The reaper used to kill a Windows orphan by process id with ``taskkill /T`` and
call a row ``gone`` as soon as the leader had exited. A descendant that had left
the leader's tree (or outlived it inside the job) survived a row marked clean.
For rows at level ``windows_job`` the reaper now opens the job by its name,
terminates it, and marks the row ``terminated`` only when the job reports no
active processes.

What this file proves: the decision logic, the name validation, and the shape of
the ``OpenJobObjectW`` wrapper against a fake kernel32, all on the host running
the tests. What it cannot prove: that real Windows behaves as the fake does.
``test_open_job_object_on_a_real_windows_host`` is the only real check and it is
meaningful only on windows-latest. As of this change it has not been run on
Windows by the author.
"""
from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

import pytest

from decisionbound import containment as C

CID = "cp_" + "ab" * 16


class FakeJob:
    """A job object double: ``counts`` is the active process count seen on each query."""

    def __init__(self, counts):
        self.counts = list(counts)
        self.terminated = 0
        self.closed = 0

    def terminate(self):
        self.terminated += 1

    def active_processes(self):
        return self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]

    def close(self):
        self.closed += 1


class FakeRegistry:
    def __init__(self, rows):
        self.rows, self.marked = rows, {}

    def running(self):
        return self.rows

    def mark(self, containment_id, state):
        self.marked[containment_id] = state


class FakeJournal:
    def __init__(self):
        self.events, self.blocked = [], []

    def record_containment_event(self, workflow_id, task_id, event, detail):
        self.events.append((event, detail))

    def block_uncontained(self, workflow_id, task_id, generation):
        self.blocked.append(task_id)


def row(level="windows_job", *, cid=CID, pid=4242, token="win:1"):
    return {"containment_id": cid, "workflow_id": "wf_x", "task_id": "stage", "generation": 1,
            "owner_pid": 1111, "owner_start_token": "owner", "leader_generation": 1,
            "level": level, "pid": pid, "pgid": pid, "start_token": token}


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows: no live processes, no job, and a recorder for taskkill."""
    state = SimpleNamespace(live={}, jobs={}, opened=[], taskkills=[], open_error=None)
    monkeypatch.setattr(C, "_IS_NT", True)
    monkeypatch.setattr(C, "process_identity", lambda pid: state.live.get(pid))

    def open_existing(name):
        state.opened.append(name)
        if state.open_error:
            raise state.open_error
        return state.jobs.get(name)

    monkeypatch.setattr(C._WindowsJob, "open_existing", staticmethod(open_existing), raising=False)
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: state.taskkills.append(argv))
    monkeypatch.setattr(C, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(C, "_CONFIRM_SECONDS", 0.05)
    return state


def reaper(rows):
    registry, journal = FakeRegistry(rows), FakeJournal()
    return C.OrphanReaper(registry, journal, term_grace_seconds=0.0), registry, journal


def test_descendants_alive_after_the_leader_exited_are_killed_through_the_job(windows):
    job = FakeJob([2, 0])  # two descendants, then empty after terminate
    windows.jobs[C._job_name(CID)] = job
    result, registry, journal = reaper([row()])
    report = result.reap(leader_generation=2)
    assert registry.marked == {CID: "terminated"}, "leader gone with a live job must not be called gone"
    assert report["terminated"] == 1 and report["gone"] == 0
    assert job.terminated == 1 and job.closed == 1
    assert windows.taskkills == [], "a windows_job row must not fall back to taskkill"
    assert journal.events[0][0] == "orphan_terminated"


def test_a_job_that_will_not_empty_is_unconfirmed_and_blocks_the_task(windows):
    job = FakeJob([3])  # never empties
    windows.jobs[C._job_name(CID)] = job
    result, registry, journal = reaper([row()])
    report = result.reap(leader_generation=2)
    assert registry.marked == {CID: "unconfirmed"} and report["unconfirmed"] == 1
    assert journal.blocked == ["stage"]
    assert job.closed == 1


def test_no_job_and_no_leader_means_kill_on_close_already_ran(windows):
    result, registry, _ = reaper([row()])
    assert result.reap(leader_generation=2)["gone"] == 1
    assert registry.marked == {CID: "gone"}
    assert windows.opened == [C._job_name(CID)]


def test_no_job_but_the_recorded_leader_is_alive_falls_back_to_taskkill(windows, monkeypatch):
    windows.live[4242] = C.ProcessIdentity(4242, "win:1")

    def kill(argv, **kw):
        windows.taskkills.append(argv)
        windows.live.pop(4242)
    monkeypatch.setattr(C.subprocess, "run", kill)
    result, registry, _ = reaper([row()])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "terminated"}
    assert windows.taskkills[0][:3] == ["taskkill", "/PID", "4242"]


def test_a_reused_process_id_is_never_killed_with_or_without_a_job(windows, monkeypatch):
    windows.live[4242] = C.ProcessIdentity(4242, "win:other")
    windows.jobs[C._job_name(CID)] = FakeJob([3, 0])
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: windows.taskkills.append(argv))
    result, registry, _ = reaper([row()])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "identity_mismatch"}
    assert windows.taskkills == [] and windows.jobs[C._job_name(CID)].terminated == 0


def test_a_job_lookup_error_is_unconfirmed_not_gone(windows):
    windows.open_error = OSError(5, "access denied")
    result, registry, _ = reaper([row()])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "unconfirmed"}


@pytest.mark.parametrize("bad", ["", "cp_x\\..\\y", "Global\\dbound-x", "cp x", "a" * 65, None, 7])
def test_a_stored_id_that_is_not_a_plain_token_is_never_opened_as_a_job_name(windows, bad):
    result, registry, _ = reaper([row(cid=bad)])
    result.reap(leader_generation=2)
    assert registry.marked == {bad: "identity_mismatch"}
    assert windows.opened == []


def test_job_name_matches_the_name_used_at_spawn():
    assert C._job_name(CID) == "Local\\dbound-" + CID
    assert C._job_name(CID).startswith("Local\\"), "never the global namespace"


def test_best_effort_rows_keep_the_taskkill_path_and_stay_unconfirmed_when_the_leader_is_gone(windows):
    result, registry, _ = reaper([row("windows_tree_best_effort")])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "unconfirmed"}
    assert windows.opened == [] and windows.taskkills == []


def test_best_effort_rows_with_a_live_matching_leader_use_taskkill(windows, monkeypatch):
    windows.live[4242] = C.ProcessIdentity(4242, "win:1")

    def kill(argv, **kw):
        windows.taskkills.append(argv)
        windows.live.pop(4242)
    monkeypatch.setattr(C.subprocess, "run", kill)
    result, registry, _ = reaper([row("windows_tree_best_effort")])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "terminated"}
    assert windows.taskkills[0][:3] == ["taskkill", "/PID", "4242"]


def test_a_recycled_pid_is_never_killed_for_a_best_effort_row(windows):
    windows.live[4242] = C.ProcessIdentity(4242, "win:999")
    result, registry, _ = reaper([row("windows_tree_best_effort")])
    result.reap(leader_generation=2)
    assert registry.marked == {CID: "identity_mismatch"} and windows.taskkills == []


# --- OpenJobObjectW wrapper against a fake kernel32 (shape only, not Windows behaviour) ---
class FakeFunction:
    def __init__(self, result):
        self.result, self.calls, self.restype, self.argtypes = result, [], None, None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def fake_kernel(handle):
    return SimpleNamespace(OpenJobObjectW=FakeFunction(handle))


def install_kernel(monkeypatch, kernel, last_error=0):
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)


def test_open_existing_asks_only_for_query_and_terminate_rights(monkeypatch):
    kernel = fake_kernel(handle=77)
    install_kernel(monkeypatch, kernel)
    job = C._WindowsJob.open_existing("Local\\dbound-x")
    assert isinstance(job, C._WindowsJob)
    access, inherit, name = kernel.OpenJobObjectW.calls[0]
    assert access == 0x0004 | 0x0008, "JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE and nothing broader"
    assert not inherit and name == "Local\\dbound-x"


def test_open_existing_returns_none_when_the_job_does_not_exist(monkeypatch):
    install_kernel(monkeypatch, fake_kernel(handle=0), last_error=2)  # ERROR_FILE_NOT_FOUND
    assert C._WindowsJob.open_existing("Local\\dbound-gone") is None


def test_open_existing_raises_for_any_other_failure(monkeypatch):
    install_kernel(monkeypatch, fake_kernel(handle=0), last_error=5)  # ERROR_ACCESS_DENIED
    with pytest.raises(OSError):
        C._WindowsJob.open_existing("Local\\dbound-x")


@pytest.mark.skipif(os.name != "nt", reason="needs real Windows job objects (windows-latest in CI)")
def test_open_job_object_on_a_real_windows_host():
    """UNVERIFIED until a windows-latest run reports it: create, reopen by name, kill, reopen."""
    name = C._job_name("cp_" + os.urandom(16).hex())
    created = C._WindowsJob.create(C.ResourceLimits(), name)
    try:
        reopened = C._WindowsJob.open_existing(name)
        assert reopened is not None
        assert reopened.active_processes() == 0
        reopened.terminate()
        reopened.close()
    finally:
        created.close()
    assert C._WindowsJob.open_existing(name) is None, "kill-on-close job must vanish with its last handle"
