"""Windows job object naming and creation, tested with fakes so it runs on every OS.

A named kernel object can be created by someone else first. ``CreateJobObjectW``
then returns a handle to the existing object and sets ``ERROR_ALREADY_EXISTS``
instead of failing, so code that ignores the error adopts an object it did not
create, changes its limits and later closes or terminates it. These tests pin
the corrected behavior: creation refuses an existing name, the name carries the
random part of the registry's containment id and nothing weaker is accepted, and
the reaper opens only a name built from an id of that shape.

What this file proves: the decision logic against a fake kernel32 on the host
running the tests. What it cannot prove: how real Windows behaves. These tests
were NOT run on Windows by their author. The Windows jobs in CI are the first
real evidence, and the module's caveat that the macOS and Windows paths are
unverified still stands.
"""
from __future__ import annotations

import ctypes
import os
import re
from types import SimpleNamespace

import pytest

from decisionbound import containment as C
from decisionbound.storage import SQLiteRepository

ERROR_ALREADY_EXISTS = 183
CID = "cp_" + "ab" * 16
NAME = "Local\\dbound-" + CID


class FakeFunction:
    """A kernel32 function double that records calls and can set the last error."""

    def __init__(self, result, world=None, sets_error=None):
        self.result, self.calls, self.world, self.sets_error = result, [], world, sets_error
        self.restype = self.argtypes = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.world is not None:
            self.world.order.append(self)
            if self.sets_error is not None:
                self.world.error = self.sets_error
        return self.result


def install(monkeypatch, *, create_result=77, create_error=None, stale_error=0):
    """Install a fake kernel32 and a fake per-thread last error. Returns the fake."""
    world = SimpleNamespace(order=[], error=stale_error)
    kernel = SimpleNamespace(
        CreateJobObjectW=FakeFunction(create_result, world, create_error),
        SetInformationJobObject=FakeFunction(1, world),
        CloseHandle=FakeFunction(1, world))

    def set_last_error(value):
        world.order.append("set_last_error")
        world.error = value

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: world.error, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", set_last_error, raising=False)
    kernel.world = world
    return kernel


# --- creation ---------------------------------------------------------------
def test_create_refuses_a_name_that_already_exists_and_leaves_that_object_alone(monkeypatch):
    kernel = install(monkeypatch, create_error=ERROR_ALREADY_EXISTS)
    with pytest.raises(OSError) as raised:
        C._WindowsJob.create(C.ResourceLimits(max_processes=4), NAME)
    assert raised.value.errno == ERROR_ALREADY_EXISTS
    assert kernel.SetInformationJobObject.calls == [], "limits must never be applied to an object we did not create"
    assert [c[0] for c in kernel.CloseHandle.calls] == [77], "the handle to the foreign object is released"


def test_create_clears_the_last_error_first_so_a_stale_error_does_not_decide(monkeypatch):
    kernel = install(monkeypatch, stale_error=ERROR_ALREADY_EXISTS)  # left over from an earlier call
    job = C._WindowsJob.create(C.ResourceLimits(), NAME)
    assert isinstance(job, C._WindowsJob), "a new object must be accepted even with an old error value"
    order = kernel.world.order
    assert order.index("set_last_error") < order.index(kernel.CreateJobObjectW)


def test_create_makes_a_non_inheritable_kill_on_close_job_for_a_new_name(monkeypatch):
    kernel = install(monkeypatch)
    C._WindowsJob.create(C.ResourceLimits(), NAME)
    attributes, name = kernel.CreateJobObjectW.calls[0]
    assert attributes is None, "no security attributes means the handle is not inheritable by children"
    assert name == NAME
    assert len(kernel.SetInformationJobObject.calls) == 1
    assert kernel.CloseHandle.calls == []


# --- the name ---------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "cp_x", "dbound", "cp_" + "a" * 31, "cp_" + "a" * 33, "CP_" + "a" * 32,
                                 "cp_" + "A" * 32, "cp_" + "a" * 31 + "\\", "x" + CID, None, 7])
def test_job_name_refuses_an_id_without_the_registry_shape(bad):
    with pytest.raises(ValueError):
        C._job_name(bad)


def test_job_name_accepts_a_registry_id_and_stays_in_the_session_namespace():
    assert C._job_name(CID) == NAME and NAME.startswith("Local\\")


def test_registry_ids_give_distinct_random_job_names(tmp_path):
    registry = C.ProcessRegistry(SQLiteRepository(tmp_path / "state.sqlite3"))
    owner = C.ProcessIdentity(1, "test")
    names = {C._job_name(registry.record_intent(workflow_id="wf", task_id="t", attempt=1, generation=1,
                                                level="windows_job", argv_sha256="0" * 64, owner=owner))
             for _ in range(40)}
    assert len(names) == 40
    assert all(re.fullmatch(r"Local\\dbound-cp_[0-9a-f]{32}", n) for n in names)


# --- spawn ------------------------------------------------------------------
class FakePopen:
    made: list = []

    def __init__(self, command, **kwargs):
        self.pid, self._handle, self.killed, self.gate = 4321, 5, False, []
        self.stdin = SimpleNamespace(write=self.gate.append, flush=lambda: None)
        FakePopen.made.append(self)

    def kill(self):
        self.killed = True

    def wait(self):
        return 1


@pytest.fixture
def spawn_env(monkeypatch):
    state = SimpleNamespace(names=[], create_error=None)

    class FakeJob:
        def assign(self, handle):
            pass

        def close(self):
            pass

    def create(cls, limits, name):
        state.names.append(name)
        if state.create_error:
            raise state.create_error
        return FakeJob()

    FakePopen.made = []
    monkeypatch.setattr(C, "_IS_NT", True)
    monkeypatch.setattr(C._WindowsJob, "create", classmethod(create))
    monkeypatch.setattr(C.subprocess, "Popen", FakePopen)
    return state


def test_spawn_without_an_id_uses_a_fresh_random_job_name_each_time(spawn_env):
    for _ in range(2):
        C.ContainedProcess.spawn(["tool"], env={})
    first, second = spawn_env.names
    assert first != second
    assert all(re.fullmatch(r"Local\\dbound-cp_[0-9a-f]{32}", n) for n in (first, second))


@pytest.mark.parametrize("bad", ["fixed-name", "cp_1", "job", "cp_" + "a" * 31])
def test_spawn_refuses_a_guessable_id_before_anything_starts(spawn_env, bad):
    with pytest.raises(C.ContainmentFailure):
        C.ContainedProcess.spawn(["tool"], env={}, containment_id=bad)
    assert spawn_env.names == [] and FakePopen.made == []


def test_spawn_fails_closed_when_the_job_name_is_already_taken(spawn_env):
    spawn_env.create_error = OSError(ERROR_ALREADY_EXISTS, "name exists")
    with pytest.raises(C.ContainmentFailure):
        C.ContainedProcess.spawn(["tool"], env={}, containment_id=CID, require_strong=True)
    assert spawn_env.names == [NAME]
    (popen,) = FakePopen.made
    assert popen.killed and popen.gate == [], "the worker must be killed before it is released"


# --- orphan reaping ---------------------------------------------------------
@pytest.fixture
def reaper_env(monkeypatch):
    state = SimpleNamespace(opened=[], jobs={}, killed=[])
    monkeypatch.setattr(C, "_IS_NT", True)
    monkeypatch.setattr(C, "process_identity", lambda pid: None)
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: state.killed.append(argv))

    def open_existing(name):
        state.opened.append(name)
        return state.jobs.get(name)

    monkeypatch.setattr(C._WindowsJob, "open_existing", staticmethod(open_existing))
    return state


def windows_row(cid):
    return {"containment_id": cid, "level": "windows_job", "pid": 4242, "pgid": 4242, "start_token": "win:1"}


@pytest.mark.parametrize("bad", ["cp_x", "cp_" + "a" * 31, "cp_" + "a" * 33, "CP_" + "a" * 32, "cp_" + "A" * 32,
                                 "someone_elses_job", "cp_" + "a" * 31 + "_"])
def test_reaper_never_opens_a_job_for_a_stored_id_without_the_registry_shape(reaper_env, bad):
    outcome = C.OrphanReaper(None, None)._examine_windows(windows_row(bad))
    assert outcome == "identity_mismatch"
    assert reaper_env.opened == [] and reaper_env.killed == []


def test_reaper_opens_exactly_the_name_built_from_the_stored_id(reaper_env):
    assert C.OrphanReaper(None, None)._examine_windows(windows_row(CID)) == "gone"
    assert reaper_env.opened == [NAME]


@pytest.mark.skipif(os.name != "nt", reason="needs real Windows job objects (windows-latest in CI)")
def test_a_second_create_of_a_live_job_name_is_refused_on_a_real_windows_host():
    """UNVERIFIED until a windows-latest run reports it: the real ERROR_ALREADY_EXISTS path."""
    name = C._job_name("cp_" + os.urandom(16).hex())
    first = C._WindowsJob.create(C.ResourceLimits(), name)
    try:
        with pytest.raises(OSError) as raised:
            C._WindowsJob.create(C.ResourceLimits(), name)
        assert raised.value.errno == ERROR_ALREADY_EXISTS
        assert first.active_processes() == 0, "the first job is untouched and still usable"
    finally:
        first.close()
