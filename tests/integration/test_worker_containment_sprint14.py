"""Real-subprocess worker containment tests (Sprint 14, gate G17).

Every test here spawns real children and real grandchildren. Nothing in the
process layer is mocked. Liveness is judged by an oracle defined in this file
(``pid_alive``), never by the module under test. Readiness is signalled by a
pidfile written by the child itself, so there are no fixed sleeps.

Only the Linux branches of the oracle and of the OS specific assertions were
executed when this file was written. The Windows and macOS branches are written
from the platform documentation and are not yet verified by any run.
"""
from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from decisionbound import containment as C
from decisionbound.background import (
    BackgroundCoordinator, CoordinatorSettings, LocalCoordinatorLeader,
)
from decisionbound.identity import init_project, load_project
from decisionbound.services import DecisionService
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import (
    ExecutionControl, ScriptedWorkflowWorker, TaskAssignment, WorkflowConflict,
    WorkflowJournal, WorkflowSpec, WorkflowSupervisor, WorkflowTask, _digest,
)

IS_WINDOWS = os.name == "nt"
SOURCE_ROOT = str(Path(__file__).resolve().parents[2] / "src")
READY_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Liveness oracle. Deliberately independent of decisionbound.containment.
# A zombie (exited but not yet reaped) counts as dead: containers without an
# init process never reap orphans, and the process can no longer run code.
# ---------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    """Return True only when ``pid`` is a process that can still execute code."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            text = Path(f"/proc/{pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return False
        return text[text.rindex(")") + 2:].split()[0] not in ("Z", "X")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                           text=True, env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")}).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_dead(pids, timeout: float = 10.0) -> list[int]:
    """Poll until every pid is dead. Return the pids that are still alive."""
    end = time.monotonic() + timeout
    alive = [p for p in pids if pid_alive(p)]
    while alive and time.monotonic() < end:
        time.sleep(0.05)
        alive = [p for p in alive if pid_alive(p)]
    return alive


def wait_for_pidfile(path: Path, timeout: float = READY_TIMEOUT) -> dict:
    """Wait for the child's atomic readiness file. Fail loudly on timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.05)
    pytest.fail(f"child never wrote readiness file {path.name} within {timeout}s")


def force_kill(pid: int) -> None:
    """Best effort cleanup used only by fixture teardown."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    for action in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
        try:
            action()
        except (ProcessLookupError, PermissionError):
            pass


def rows(store) -> list[dict]:
    """Read the process registry table directly, bypassing the module under test."""
    with store._connection() as db:
        return [dict(r) for r in db.execute("SELECT * FROM contained_processes ORDER BY rowid")]


# ---------------------------------------------------------------------------
# Child programs. They are inline so no package import is needed in the child.
# ---------------------------------------------------------------------------
_PIDFILE = '''
def write_ready(path, **ids):
    import json, os
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(ids, handle)
    os.replace(tmp, path)
'''

TREE_CHILD = _PIDFILE + '''
import os, subprocess, sys, time
pidfile = sys.argv[1]
sys.stdin.read()
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
write_ready(pidfile, child=os.getpid(), grandchild=grandchild.pid)
time.sleep(600)
'''

CRASH_THEN_PREPARED = _PIDFILE + '''
import json, os, subprocess, sys, time
pidfile, evidence = sys.argv[1], sys.argv[2]
assignment = json.loads(sys.stdin.read())
if assignment["attempt"] == 1:
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    write_ready(pidfile, child=os.getpid(), grandchild=grandchild.pid)
    os._exit(37)
print(json.dumps({"status": "prepared", "evidence_sha256": evidence}))
'''

TRAP_TERM_AND_REPORT = _PIDFILE + '''
import json, os, signal, sys, time
pidfile = sys.argv[1]
def on_term(signum, frame):
    sys.stdout.write(json.dumps({"status": "mock_passed"}) + "\\n")
    sys.stdout.flush()
    os._exit(0)
signal.signal(signal.SIGTERM, on_term)
sys.stdin.read()
write_ready(pidfile, child=os.getpid())
time.sleep(600)
'''

IGNORE_TERM = _PIDFILE + '''
import os, signal, sys, time
pidfile = sys.argv[1]
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdin.read()
write_ready(pidfile, child=os.getpid())
time.sleep(600)
'''

DUMP_ENV = '''
import json, os, sys
sys.stdin.read()
with open(sys.argv[1], "w") as handle:
    json.dump(sorted(os.environ), handle)
print(json.dumps({"status": "mock_passed"}))
'''

DUMP_STDIN = '''
import json, sys
raw = sys.stdin.read()
with open(sys.argv[1], "w") as handle:
    handle.write(raw)
print(json.dumps({"status": "mock_passed"}))
'''

FLOOD = _PIDFILE + '''
import os, sys, time
pidfile = sys.argv[1]
sys.stdin.read()
write_ready(pidfile, child=os.getpid())
sys.stdout.write("x" * 10_000_000)
sys.stdout.flush()
time.sleep(600)
'''

SPIN = _PIDFILE + '''
import os, sys
pidfile = sys.argv[1]
sys.stdin.read()
write_ready(pidfile, child=os.getpid())
while True:
    pass
'''

OK_RECEIPT = '''
import json, sys
sys.stdin.read()
print(json.dumps({"status": "mock_passed"}))
'''

SENTINEL_CHILD = '''
import sys
open(sys.argv[1], "w").write("spawned")
'''

COORDINATOR = '''
import sys
from pathlib import Path
from decisionbound.background import BackgroundCoordinator, CoordinatorSettings
from decisionbound.containment import ContainedWorker, ProcessRegistry
from decisionbound.storage import SQLiteRepository
database, project_id, pidfile, child_script = sys.argv[1:5]
store = SQLiteRepository(Path(database))
worker = ContainedWorker([sys.executable, "-c", child_script, pidfile],
                         registry=ProcessRegistry(store), deadline_seconds=600)
BackgroundCoordinator(store, project_id=project_id, workers={"code": worker},
                      settings=CoordinatorSettings(max_rss_mib=None, task_lease_seconds=5,
                                                   leader_lease_seconds=10, poll_seconds=0.1)
                      ).run(max_seconds=300)
'''


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    store = SQLiteRepository(tmp_path / "data" / "state.sqlite3")
    home = tmp_path / "project"
    home.mkdir()
    project = init_project(home, store)
    tracked: list[int] = []
    namespace = SimpleNamespace(store=store, home=home, project=project,
                                journal=WorkflowJournal(store), registry=C.ProcessRegistry(store),
                                tmp=tmp_path, tracked=tracked)
    yield namespace
    # A surviving child would hold the SQLite file open and break tmp cleanup
    # on Windows, so kill everything still believed alive.
    for pid in tracked:
        force_kill(pid)
    try:
        for row in rows(store):
            if row["state"] in ("spawning", "running") and row["pid"]:
                force_kill(row["pid"])
    except Exception:
        pass


def make_workflow(env, kind="mock", *, attempts=1, cost=0.0, budget=0.0, key="contain_run",
                  description="Inspect repository security boundaries"):
    spec = WorkflowSpec((WorkflowTask("stage", kind, description, max_attempts=attempts,
                                      max_cost_usd=cost),), max_workers=1, max_total_cost_usd=budget)
    return env.journal.create(project_path=env.home, spec=spec, idempotency_key=key)


def contained(env, script, *args, deadline=30, grace=3, limits=None, **options):
    return C.ContainedWorker([sys.executable, "-c", script, *map(str, args)], registry=env.registry,
                             deadline_seconds=deadline, term_grace_seconds=grace, limits=limits, **options)


def supervise(env, workers, wid, **options):
    supervisor = WorkflowSupervisor(env.journal, workers=workers)
    options.setdefault("lease_seconds", 30)
    options.setdefault("max_wall_seconds", 60)
    return supervisor.run(wid, **options)


def run_in_thread(function):
    """Run ``function`` in a thread and surface its result or exception on join."""
    box: dict = {}

    def target():
        try:
            box["value"] = function()
        except BaseException as exc:  # re-raised in the test thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def join(timeout=30.0):
        thread.join(timeout)
        assert not thread.is_alive(), "supervisor thread did not finish"
        if "error" in box:
            raise box["error"]
        return box.get("value")
    return join


def task_state(env, wid):
    return env.journal.status(wid)["tasks"][0]


def events_named(env, wid, name):
    return [e for e in env.journal.events(wid) if e["event"] == name]


def prepare_digest(env, description="Inspect repository security boundaries"):
    project = load_project(env.home, env.store)
    return _digest(DecisionService(env.store).prepare(description, project, 1200).to_dict())


def spawn_bystander():
    """A real process in its own group, standing in for an orphaned worker."""
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS
              else {"start_new_session": True})
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kwargs)


def dead_owner():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return C.ProcessIdentity(proc.pid, "owner-token-of-a-dead-process")


def register(env, pid, *, token=None, owner=None, leader_generation=None, workflow_id="wf_manual",
             task_id="stage", generation=1, attempt=1):
    cid = env.registry.record_intent(workflow_id=workflow_id, task_id=task_id, attempt=attempt,
                                     generation=generation, level=C.probe_capabilities().level,
                                     argv_sha256="0" * 64, owner=owner,
                                     leader_generation=leader_generation)
    identity = C.process_identity(pid) if token is None else C.ProcessIdentity(pid, token)
    env.registry.record_started(cid, pid=pid, pgid=pid, identity=identity)
    return cid


def reaper(env):
    return C.OrphanReaper(env.registry, env.journal, term_grace_seconds=1.0)


# ---------------------------------------------------------------------------
# Section 7 of the design: the eleven required real-subprocess tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["mock", "code"])
def test_01_deadline_kills_child_and_grandchild(env, kind):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env, kind, cost=0.5, budget=1.0)
    worker = contained(env, TREE_CHILD, pidfile, deadline=3, grace=1)
    started = time.monotonic()
    supervise(env, {kind: worker}, wid)
    elapsed = time.monotonic() - started
    info = json.loads(pidfile.read_text()) if pidfile.exists() else pytest.fail("child never became ready before the deadline")
    env.tracked += [info["child"], info["grandchild"]]
    assert elapsed < 3 + 10
    assert wait_dead([info["child"], info["grandchild"]], timeout=2) == []
    entry = task_state(env, wid)
    assert entry["state"] == ("blocked" if kind == "code" else "failed")
    assert entry["error_type"] == "WorkerDeadlineExceeded"
    assert len(events_named(env, wid, "claimed")) == 1
    assert env.journal.status(wid)["reserved_cost_usd"] == 0.5
    assert [r["state"] for r in rows(env.store)] == ["terminated"]


def test_02_cancel_revokes_claim_and_kills_tree(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    worker = contained(env, TREE_CHILD, pidfile, deadline=120)
    join = run_in_thread(lambda: supervise(env, {"mock": worker}, wid))
    info = wait_for_pidfile(pidfile)
    env.tracked += [info["child"], info["grandchild"]]
    assert pid_alive(info["child"]) and pid_alive(info["grandchild"])
    started = time.monotonic()
    env.journal.cancel(wid)
    join(timeout=20)
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    assert time.monotonic() - started < 5
    entry = task_state(env, wid)
    assert entry["state"] == "cancelled" and entry["receipt_sha256"] is None
    assert [r["state"] for r in rows(env.store)] == ["terminated"]


def test_03_worker_crash_leaves_no_grandchild_and_read_only_task_retries(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env, "prepare", attempts=2)
    worker = contained(env, CRASH_THEN_PREPARED, pidfile, prepare_digest(env), deadline=60)
    result = supervise(env, {"prepare": worker}, wid)
    info = json.loads(pidfile.read_text())
    env.tracked += [info["child"], info["grandchild"]]
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    failed = events_named(env, wid, "failed_attempt")
    assert [e["metadata"] for e in failed] == [{"error_type": "WorkerCrashed", "retry_eligible": True}]
    assert result["state"] == "verified_stages"
    assert task_state(env, wid)["attempt"] == 2
    assert [r["state"] for r in rows(env.store)] == ["exited", "exited"]


def test_04_coordinator_restart_reaps_orphans(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env, "code")
    coordinator = subprocess.Popen(
        [sys.executable, "-c", COORDINATOR, str(env.store.path), env.project.project_id,
         str(pidfile), TREE_CHILD],
        env=dict(os.environ, PYTHONPATH=SOURCE_ROOT),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        info = wait_for_pidfile(pidfile)
        env.tracked += [info["child"], info["grandchild"]]
        coordinator.kill()
        coordinator.wait(timeout=15)
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.wait(timeout=15)
    if not IS_WINDOWS:
        # Without a lifeline pipe, a hard-killed coordinator leaves its worker
        # running until the next coordinator reaps it. That is the documented limit.
        assert pid_alive(info["child"]) and pid_alive(info["grandchild"])
    # The dead coordinator's leadership lease would take up to ten seconds to
    # lapse. Expire it directly, which is exactly what elapsed time does.
    with env.store._connection() as db:
        db.execute("UPDATE local_coordinator_lease SET expires_at=0")
    restarted = BackgroundCoordinator(env.store, project_id=env.project.project_id,
        settings=CoordinatorSettings(max_rss_mib=None, task_lease_seconds=5, leader_lease_seconds=10)
        ).run(once=True)
    assert wait_dead([info["child"], info["grandchild"]], timeout=10) == []
    states = [r["state"] for r in rows(env.store)]
    if IS_WINDOWS:
        assert states == ["gone"]  # job close already killed the tree
    else:
        assert states == ["terminated"] and restarted["orphans_reaped"] >= 1
    end = time.monotonic() + 30
    while time.monotonic() < end and env.journal.reconcile(wid)["tasks"][0]["state"] != "blocked":
        time.sleep(0.25)
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "unknown_write_outcome"
    assert len(events_named(env, wid, "claimed")) == 1


def test_05_late_result_is_rejected_and_recorded(env):
    wid = make_workflow(env)
    lease = env.journal.claim(wid, worker_kinds={"mock"}, now=time.time() - 100, lease_seconds=5)
    assert lease is not None
    receipt = contained(env, OK_RECEIPT).execute(lease.assignment())
    assert receipt.status == "mock_passed"  # a real child produced a valid receipt
    before = env.journal.reconcile(wid)  # the long expired lease is reconciled away
    with pytest.raises(WorkflowConflict):
        env.journal.finish(lease, state="mock_passed", receipt=receipt)
    after = env.journal.status(wid)
    assert after["tasks"] == before["tasks"] and after["tasks"][0]["receipt_sha256"] is None
    env.journal.note_late_result(lease, "finish_rejected")
    late = events_named(env, wid, "late_result_rejected")
    assert len(late) == 1 and late[0]["metadata"]["stale_generation"] == lease.generation
    assert env.journal.status(wid)["tasks"] == before["tasks"]  # the note changed no task state


def test_05b_supervisor_records_late_result_when_claim_was_lost(env):
    wid = make_workflow(env)
    join = run_in_thread(lambda: supervise(env, {"mock": ScriptedWorkflowWorker(delay=2.0)}, wid,
                                           lease_seconds=5))
    end = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < end and task_state(env, wid)["state"] != "leased":
        time.sleep(0.02)
    with env.store._connection() as db:  # another process took the lease away
        db.execute("UPDATE workflow_tasks SET lease_until=0")
    join()
    late = events_named(env, wid, "late_result_rejected")
    assert len(late) == 1 and late[0]["metadata"]["stale_generation"] == 1


@pytest.mark.skipif(IS_WINDOWS, reason="TerminateJobObject delivers no catchable signal")
def test_06_result_produced_while_being_cancelled_is_discarded(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    worker = contained(env, TRAP_TERM_AND_REPORT, pidfile, deadline=120)
    join = run_in_thread(lambda: supervise(env, {"mock": worker}, wid))
    info = wait_for_pidfile(pidfile)
    env.tracked.append(info["child"])
    env.journal.cancel(wid)
    join(timeout=20)
    assert wait_dead([info["child"]], timeout=5) == []
    entry = task_state(env, wid)
    assert entry["state"] == "cancelled" and entry["receipt_sha256"] is None
    assert not [e for e in events_named(env, wid, "finished") if e["task_id"] == "stage"]


def test_07_pid_reuse_is_never_killed(env):
    bystander = spawn_bystander()
    env.tracked.append(bystander.pid)
    cid = register(env, bystander.pid, token="not-the-real-start-token", owner=dead_owner())
    report = reaper(env).reap(leader_generation=1)
    assert pid_alive(bystander.pid), "reaper signalled a process whose start token did not match"
    assert [r["state"] for r in rows(env.store)] == ["identity_mismatch"]
    assert report["identity_mismatch"] == 1 and report["terminated"] == 0
    assert cid in {r["containment_id"] for r in rows(env.store)}
    bystander.kill()
    bystander.wait()


def test_08_sigterm_ignored_dies_only_after_grace_by_force(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    deadline, grace = 3, 2
    worker = contained(env, IGNORE_TERM, pidfile, deadline=deadline, grace=grace)
    started = time.monotonic()
    supervise(env, {"mock": worker}, wid)
    elapsed = time.monotonic() - started
    info = json.loads(pidfile.read_text()) if pidfile.exists() else pytest.fail("child never became ready before the deadline")
    env.tracked.append(info["child"])
    assert wait_dead([info["child"]], timeout=2) == []
    assert elapsed < deadline + grace + 10
    if IS_WINDOWS:
        return  # TerminateJobObject is immediate: there is no grace phase to observe
    assert elapsed >= deadline + grace - 0.25, "child died before the grace period: SIGTERM was not ignored"
    assert task_state(env, wid)["error_type"] == "WorkerDeadlineExceeded"


def test_09_environment_is_an_allowlist(env, monkeypatch):
    monkeypatch.setenv("DBOUND_SECRET_SENTINEL", "must-not-leak")
    monkeypatch.setenv("DBOUND_PASSTHROUGH_OK", "1")
    dump = env.tmp / "env.json"
    wid = make_workflow(env)
    worker = contained(env, DUMP_ENV, dump, env_passthrough=("DBOUND_PASSTHROUGH_OK",))
    supervise(env, {"mock": worker}, wid)
    names = json.loads(dump.read_text())
    assert "DBOUND_SECRET_SENTINEL" not in names
    assert "DBOUND_PASSTHROUGH_OK" in names and "PATH" in names
    if not IS_WINDOWS:
        assert "HOME" not in names


def test_10_output_flood_kills_worker_and_quarantines(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    worker = contained(env, FLOOD, pidfile, deadline=60,
                       limits=C.ResourceLimits(max_output_bytes=65536))
    started = time.monotonic()
    supervise(env, {"mock": worker}, wid)
    assert time.monotonic() - started < 20
    info = wait_for_pidfile(pidfile, timeout=1)
    env.tracked.append(info["child"])
    assert wait_dead([info["child"]], timeout=5) == []
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "invalid_receipt"
    assert events_named(env, wid, "quarantined")[0]["metadata"] == {"reason": "invalid_receipt"}
    assert [r["state"] for r in rows(env.store)] == ["terminated"]


def test_11_weak_containment_is_refused_for_code_tasks(env, monkeypatch):
    sentinel = env.tmp / "spawned.txt"
    weak = C.ContainmentCapabilities(level="windows_tree_best_effort", enforced_limits=frozenset())
    monkeypatch.setattr(C, "probe_capabilities", lambda: weak)
    wid = make_workflow(env, "code")
    worker = contained(env, SENTINEL_CHILD, sentinel, require_strong=False)  # code kind overrides this
    supervise(env, {"code": worker}, wid)
    assert not sentinel.exists(), "a code task was spawned without strong containment"
    assert rows(env.store) == []
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "containment_failed"
    assert events_named(env, wid, "quarantined")[0]["metadata"] == {"reason": "containment_failed"}


# ---------------------------------------------------------------------------
# Additional purposeful tests for behaviour the design promises
# ---------------------------------------------------------------------------
def test_stdin_carries_the_assignment_but_no_claim_secrets(env):
    dump = env.tmp / "stdin.json"
    wid = make_workflow(env)
    lease = env.journal.claim(wid, worker_kinds={"mock"}, lease_seconds=30)
    contained(env, DUMP_STDIN, dump).execute(lease.assignment())
    raw = dump.read_text()
    payload = json.loads(raw)
    assert set(payload) == {"workflow_id", "project_id", "project_path", "task", "attempt",
                            "reserved_cost_usd", "handoffs"}
    assert payload["workflow_id"] == wid and payload["task"]["task_id"] == "stage"
    assert lease.owner_token not in raw and "owner_token" not in raw and "generation" not in raw


@pytest.mark.skipif(IS_WINDOWS, reason="RLIMIT_CPU is exercised on POSIX; Windows uses a job time limit")
def test_cpu_limit_is_enforced_by_the_operating_system(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    worker = contained(env, SPIN, pidfile, deadline=60, limits=C.ResourceLimits(max_cpu_seconds=1))
    started = time.monotonic()
    supervise(env, {"mock": worker}, wid)
    assert time.monotonic() - started < 25
    info = json.loads(pidfile.read_text())
    env.tracked.append(info["child"])
    assert wait_dead([info["child"]], timeout=2) == []
    assert task_state(env, wid)["error_type"] == "WorkerCrashed"


def test_worker_started_event_records_enforced_limits_without_secrets(env, monkeypatch):
    monkeypatch.setenv("DBOUND_SECRET_SENTINEL", "must-not-leak")
    wid = make_workflow(env)
    limits = C.ResourceLimits(max_cpu_seconds=120)  # enforced on Linux, macOS and Windows
    supervise(env, {"mock": contained(env, OK_RECEIPT, limits=limits)}, wid)
    started = events_named(env, wid, "worker_started")
    assert len(started) == 1
    metadata = started[0]["metadata"]
    assert metadata["level"] == C.probe_capabilities().level
    assert "max_cpu_seconds" in metadata["enforced"] and "containment_id" in metadata
    text = json.dumps(env.journal.events(wid))
    assert "must-not-leak" not in text and sys.executable not in text


def test_abort_event_kills_the_worker_immediately(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    abort = threading.Event()
    worker = contained(env, TREE_CHILD, pidfile, deadline=120)
    join = run_in_thread(lambda: supervise(env, {"mock": worker}, wid, abort_event=abort))
    info = wait_for_pidfile(pidfile)
    env.tracked += [info["child"], info["grandchild"]]
    started = time.monotonic()
    abort.set()
    join(timeout=20)
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    assert time.monotonic() - started < 5
    entry = task_state(env, wid)
    assert entry["state"] == "failed" and entry["error_type"] == "WorkerCancelled"


def test_stop_event_lets_the_worker_drain_for_the_grace_period_then_cancels(env):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    stop = threading.Event()
    worker = contained(env, TREE_CHILD, pidfile, deadline=120)
    join = run_in_thread(lambda: supervise(env, {"mock": worker}, wid, stop_event=stop,
                                           stop_grace_seconds=1))
    info = wait_for_pidfile(pidfile)
    env.tracked += [info["child"], info["grandchild"]]
    started = time.monotonic()
    stop.set()
    join(timeout=20)
    elapsed = time.monotonic() - started
    assert 0.9 <= elapsed < 10
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    assert task_state(env, wid)["error_type"] == "WorkerCancelled"


def test_thread_workers_keep_working_through_the_default_controlled_path():
    worker = ScriptedWorkflowWorker()
    assignment = TaskAssignment("wf_x", "prj_x", Path("."), WorkflowTask("stage", "mock", "Describe it"), 1, 0.0)
    control = ExecutionControl(workflow_id="wf_x", task_id="stage", attempt=1, generation=1)
    assert worker.execute_controlled(assignment, control).status == "mock_passed"
    assert not control.cancel.is_set()
    control.request_cancel("first_reason")
    control.request_cancel("second_reason")
    assert control.cancel.is_set() and control.reason == "first_reason"
    assert not hasattr(control, "owner_token")


def test_journal_containment_helpers(env):
    wid = make_workflow(env, "code")
    lease = env.journal.claim(wid, worker_kinds={"code"}, lease_seconds=30)
    env.journal.record_containment_event(wid, "stage", "worker_started", {"level": "posix_session"})
    with pytest.raises(ValueError):
        env.journal.record_containment_event(wid, "stage", "claimed", {})
    assert env.journal.block_uncontained(wid, "stage", lease.generation + 1) is False
    assert task_state(env, wid)["state"] == "leased"
    assert env.journal.block_uncontained(wid, "stage", lease.generation) is True
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "containment_failed"
    assert env.journal.block_uncontained(wid, "stage", lease.generation) is False


def test_registry_is_lazy_additive_and_leaves_schema_version_alone(tmp_path):
    store = SQLiteRepository(tmp_path / "state.sqlite3")

    def has_table():
        with store._connection() as db:
            return db.execute("SELECT 1 FROM sqlite_master WHERE name='contained_processes'").fetchone() is not None
    assert not has_table()
    assert C.ProcessRegistry(store, create=False).running() == [] and not has_table()
    C.ProcessRegistry(store)
    assert has_table()
    with store._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {r[1] for r in db.execute("PRAGMA table_info(contained_processes)")}
    assert not columns & {"argv", "env", "owner_token", "environment"}
    SQLiteRepository(tmp_path / "state.sqlite3")  # core schema checks still accept the database


def test_identity_and_capability_probes():
    mine = C.process_identity(os.getpid())
    assert mine is not None and mine == C.process_identity(os.getpid()) and mine.start_token
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert C.process_identity(child.pid) is None
    caps = C.probe_capabilities()
    if sys.platform.startswith("linux"):
        assert caps.level == "posix_session" and caps.strong
        assert {"max_cpu_seconds", "max_open_files", "max_output_bytes"} <= caps.enforced_limits
    assert not C.ContainmentCapabilities("windows_tree_best_effort", frozenset()).strong
    assert C.ContainmentCapabilities("windows_job", frozenset()).strong


def test_validation_of_limits_and_worker_configuration(env):
    for bad in ({"max_cpu_seconds": 0}, {"max_memory_mib": -1}, {"max_open_files": True},
                {"max_processes": 0}, {"max_output_bytes": 0}, {"max_output_bytes": 10**9}):
        with pytest.raises(ValueError):
            C.ResourceLimits(**bad)
    argv = [sys.executable, "-c", "pass"]
    for bad in ({"argv": []}, {"argv": "python"}, {"argv": [sys.executable, 3]},
                {"deadline_seconds": 0}, {"deadline_seconds": 10**6},
                {"term_grace_seconds": -1}, {"registry": object()}):
        options = dict(argv=argv, registry=env.registry, deadline_seconds=5)
        options.update(bad)
        with pytest.raises((ValueError, TypeError)):
            C.ContainedWorker(options.pop("argv"), **options)


def test_a_requested_limit_the_host_cannot_enforce_fails_closed(env):
    # POSIX has no per-tree process cap here, and Windows job objects have no open file cap.
    unenforceable = C.ResourceLimits(max_open_files=64) if IS_WINDOWS else C.ResourceLimits(max_processes=8)
    wid = make_workflow(env)
    lease = env.journal.claim(wid, worker_kinds={"mock"}, lease_seconds=30)
    worker = contained(env, OK_RECEIPT, limits=unenforceable)
    with pytest.raises(C.ContainmentFailure):
        worker.execute(lease.assignment())
    assert rows(env.store) == []


def test_reaper_marks_gone_when_nothing_is_left(env):
    corpse = subprocess.Popen([sys.executable, "-c", "pass"])
    corpse.wait()
    register(env, corpse.pid, token="was-running-once", owner=dead_owner())
    report = reaper(env).reap(leader_generation=1)
    assert [r["state"] for r in rows(env.store)] == ["gone"] and report["gone"] == 1


def test_reaper_leaves_rows_of_a_live_owner_alone(env):
    bystander = spawn_bystander()
    env.tracked.append(bystander.pid)
    register(env, bystander.pid)  # owner is this live process, no leadership bound
    report = reaper(env).reap(leader_generation=5)
    assert pid_alive(bystander.pid) and report["examined"] == 0
    assert [r["state"] for r in rows(env.store)] == ["running"]
    bystander.kill()
    bystander.wait()


def test_reaper_kills_a_worker_started_under_an_older_leadership_even_if_its_owner_lives(env):
    bystander = spawn_bystander()
    env.tracked.append(bystander.pid)
    register(env, bystander.pid, leader_generation=1)
    report = reaper(env).reap(leader_generation=2)
    assert wait_dead([bystander.pid], timeout=5) == []
    assert report["terminated"] == 1 and [r["state"] for r in rows(env.store)] == ["terminated"]
    bystander.wait()


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX process groups: the leader can die while the group lives")
def test_reaper_does_not_guess_when_the_leader_is_gone_but_its_group_lives(env):
    pidfile = env.tmp / "tree.json"
    script = _PIDFILE + '''
import os, subprocess, sys
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
write_ready(sys.argv[1], child=os.getpid(), grandchild=gc.pid)
'''
    leader = subprocess.Popen([sys.executable, "-c", script, str(pidfile)], start_new_session=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    info = wait_for_pidfile(pidfile)
    env.tracked += [info["grandchild"], leader.pid]
    token = C.process_identity(leader.pid)
    leader.wait()
    wid = make_workflow(env, "code")
    lease = env.journal.claim(wid, worker_kinds={"code"}, lease_seconds=30)
    register(env, leader.pid, token=token.start_token if token else "gone", owner=dead_owner(),
             workflow_id=wid, generation=lease.generation)
    report = reaper(env).reap(leader_generation=1)
    assert pid_alive(info["grandchild"]), "reaper killed a group whose leader identity could not be verified"
    assert [r["state"] for r in rows(env.store)] == ["unconfirmed"] and report["unconfirmed"] == 1
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "containment_failed"


def test_reaper_treats_an_unfinished_spawn_of_a_dead_owner_as_unconfirmed(env):
    wid = make_workflow(env, "code")
    lease = env.journal.claim(wid, worker_kinds={"code"}, lease_seconds=30)
    env.registry.record_intent(workflow_id=wid, task_id="stage", attempt=1, generation=lease.generation,
                               level=C.probe_capabilities().level, argv_sha256="0" * 64, owner=dead_owner())
    report = reaper(env).reap(leader_generation=1)
    assert [r["state"] for r in rows(env.store)] == ["unconfirmed"] and report["unconfirmed"] == 1
    assert task_state(env, wid)["error_type"] == "containment_failed"


def test_reap_orphans_cli_is_explicit_and_reports_counts_only(env):
    bystander = spawn_bystander()
    env.tracked.append(bystander.pid)
    register(env, bystander.pid, owner=dead_owner())
    completed = subprocess.run(
        [sys.executable, "-m", "decisionbound.cli", "workflow", "reap-orphans"],
        env=dict(os.environ, DBOUND_HOME=str(env.store.path.parent), PYTHONPATH=SOURCE_ROOT),
        capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["terminated"] == 1 and "owner_token" not in completed.stdout
    assert wait_dead([bystander.pid], timeout=5) == []
    bystander.wait()
    assert [r["state"] for r in rows(env.store)] == ["terminated"]
    assert not LocalCoordinatorLeader(env.store).status()["running"]  # leadership was released


def test_coordinator_reports_orphan_counters_even_when_containment_was_never_used(env):
    with env.store._connection() as db:
        db.execute("DROP TABLE contained_processes")
    result = BackgroundCoordinator(env.store, project_id=env.project.project_id,
                                   settings=CoordinatorSettings(max_rss_mib=None)).run(once=True)
    assert result["orphans_reaped"] == 0 and result["orphans_unconfirmed"] == 0
    with env.store._connection() as db:  # the coordinator must not create the table
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='contained_processes'").fetchone() is None


def test_stop_grace_setting_is_bounded():
    assert CoordinatorSettings().worker_stop_grace_seconds == 10
    CoordinatorSettings(worker_stop_grace_seconds=0)
    CoordinatorSettings(worker_stop_grace_seconds=300)
    for bad in (-1, 301, True, 1.5):
        with pytest.raises(ValueError):
            CoordinatorSettings(worker_stop_grace_seconds=bad)


# ---------------------------------------------------------------------------
# Review round: findings 1, 2, 6 and 9 of the independent review
# ---------------------------------------------------------------------------
def test_a_supervisor_error_cancels_running_workers_instead_of_waiting_out_the_deadline(env, monkeypatch):
    pidfile = env.tmp / "tree.json"
    wid = make_workflow(env)
    worker = contained(env, TREE_CHILD, pidfile, deadline=120)
    calls = {"n": 0}
    real = env.journal.heartbeat

    def flaky(lease, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 3 and pidfile.exists():
            raise RuntimeError("simulated host error during heartbeat")
        return real(lease, **kwargs)

    monkeypatch.setattr(env.journal, "heartbeat", flaky)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="simulated host error"):
        supervise(env, {"mock": worker}, wid, lease_seconds=30)
    assert time.monotonic() - started < 25, "run() waited for the worker deadline"
    info = wait_for_pidfile(pidfile, timeout=1)
    env.tracked.extend([info["child"], info["grandchild"]])
    assert wait_dead([info["child"], info["grandchild"]], timeout=10) == []


@pytest.mark.parametrize("raw", [
    b'{"status":"prepared","cost_usd":' + b"9" * 400 + b"}",
    b"[" * 50000,
    b'{"status":"blocked","status":"mock_passed"}',
    b'{"status":"prepared","cost_usd":1e999}',
    b"\xff\xfe",
])
def test_malformed_receipts_are_always_invalid_output_never_a_retryable_error(raw):
    with pytest.raises(C.WorkerOutputInvalid):
        C._parse_receipt(raw)


def test_a_huge_integer_cost_is_quarantined_not_retried(env):
    wid = make_workflow(env, attempts=3)
    script = ('import json,sys\nsys.stdin.read()\n'
              'sys.stdout.write(\'{"status":"mock_passed","cost_usd":\' + "9"*400 + "}\\n")\n')
    supervise(env, {"mock": contained(env, script)}, wid)
    entry = task_state(env, wid)
    assert entry["state"] == "blocked" and entry["error_type"] == "invalid_receipt"
    assert len(rows(env.store)) == 1, "a malformed receipt must not be retried"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX signalling guard")
@pytest.mark.parametrize("which", ["self", "pgid_differs", "init"])
def test_reaper_never_signals_group_ids_it_did_not_start(env, which):
    bystander = spawn_bystander()
    env.tracked.append(bystander.pid)
    target = {"self": os.getpid(), "pgid_differs": bystander.pid, "init": 1}[which]
    cid = env.registry.record_intent(workflow_id="wf_manual", task_id="stage", attempt=1, generation=1,
                                     level="posix_session", argv_sha256="0" * 64,
                                     owner=dead_owner(), leader_generation=1)
    pgid = bystander.pid + 1 if which == "pgid_differs" else target
    env.registry.record_started(cid, pid=target, pgid=pgid, identity=C.process_identity(target) or
                                C.ProcessIdentity(target, "x"))
    report = reaper(env).reap(leader_generation=2)
    assert report["identity_mismatch"] == 1
    assert pid_alive(bystander.pid) and pid_alive(os.getpid())
    assert [r["state"] for r in rows(env.store)] == ["identity_mismatch"]
    bystander.kill()
    bystander.wait()


def test_recording_intent_refuses_when_the_own_identity_cannot_be_read(env, monkeypatch):
    monkeypatch.setattr(C, "process_identity", lambda pid: None)
    with pytest.raises(C.ContainmentFailure):
        env.registry.record_intent(workflow_id="wf_manual", task_id="stage", attempt=1, generation=1,
                                   level="posix_session", argv_sha256="0" * 64)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX process groups")
def test_ps_group_listing_sees_a_zombie_only_group_as_not_runnable(env):
    """macOS answers EPERM for such a group, and the reaper then relies on this listing."""
    proc = spawn_bystander()
    env.tracked.append(proc.pid)
    live = C._ps_group_states(proc.pid)
    assert live and not any(state.startswith("Z") for state in live)
    os.killpg(proc.pid, signal.SIGKILL)
    end = time.monotonic() + 5
    while time.monotonic() < end and not all(s.startswith("Z") for s in C._ps_group_states(proc.pid) or ["Z"]):
        time.sleep(0.05)
    assert all(state.startswith("Z") for state in C._ps_group_states(proc.pid))  # unreaped zombie
    proc.wait()


# ---------------------------------------------------------------------------
# Hangup and quit signals reach the same orderly stop as SIGTERM
# ---------------------------------------------------------------------------
HANGUP_SIGNALS = [name for name in ("SIGHUP", "SIGQUIT") if hasattr(signal, name)]


def _default_hangup_dispositions():
    """Give SIGHUP and SIGQUIT their default action, whatever the launcher inherited."""
    for name in HANGUP_SIGNALS:
        signal.signal(getattr(signal, name), signal.SIG_DFL)


@pytest.fixture
def default_hangup_dispositions():
    """Serve tests assume a launcher that did not ignore SIGHUP or SIGQUIT, as nohup does."""
    before = {getattr(signal, name): signal.getsignal(getattr(signal, name)) for name in HANGUP_SIGNALS}
    _default_hangup_dispositions()
    yield
    for number, handler in before.items():
        signal.signal(number, handler)


# Runs the real `dbound workflow serve --contained` command with a stand-in
# contained worker, so a real child and grandchild exist to be cleaned up. The
# stop grace is shortened to one second so the test does not wait ten.
SERVE_HARNESS = '''
import dataclasses, sys
from decisionbound import background, worker_child
from decisionbound.containment import ContainedWorker
pidfile, child_script, home = sys.argv[1:4]

@dataclasses.dataclass(frozen=True)
class QuickStop(background.CoordinatorSettings):
    worker_stop_grace_seconds: int = 1

background.CoordinatorSettings = QuickStop
worker_child.child_can_import = lambda: True
worker_child.contained_preparation_worker = lambda registry, **_: ContainedWorker(
    [sys.executable, "-c", child_script, pidfile], registry=registry, deadline_seconds=600)
from decisionbound.cli import app
app(["workflow", "serve", "--path", home, "--poll-seconds", "0.1", "--contained"])
'''


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX signals")
@pytest.mark.parametrize("name", ["SIGTERM"] + HANGUP_SIGNALS)
def test_serve_stops_in_order_on_terminate_hangup_and_quit(env, name):
    pidfile = env.tmp / "tree.json"
    make_workflow(env, "prepare")
    service = subprocess.Popen(
        [sys.executable, "-c", SERVE_HARNESS, str(pidfile), TREE_CHILD, str(env.home)],
        env=dict(os.environ, PYTHONPATH=SOURCE_ROOT, DBOUND_HOME=str(env.store.path.parent)),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, preexec_fn=None if IS_WINDOWS else _default_hangup_dispositions)
    try:
        info = wait_for_pidfile(pidfile)
        env.tracked += [info["child"], info["grandchild"], service.pid]
        assert LocalCoordinatorLeader(env.store).status()["running"]
        os.kill(service.pid, getattr(signal, name))
        output, _ = service.communicate(timeout=30)
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=15)
    assert service.returncode == 0, f"{name} ended the service with status {service.returncode}"
    assert json.loads(output)["stopped"] is True
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    assert not LocalCoordinatorLeader(env.store).status()["running"]
    assert [r["state"] for r in rows(env.store)] == ["terminated"]


@pytest.mark.skipif(IS_WINDOWS or "SIGHUP" not in HANGUP_SIGNALS, reason="POSIX signals")
def test_serve_hangup_with_the_terminal_gone_still_exits_cleanly(env):
    """After a hangup the output pipe is often closed. Only the summary may be lost."""
    pidfile = env.tmp / "tree.json"
    make_workflow(env, "prepare")
    service = subprocess.Popen(
        [sys.executable, "-c", SERVE_HARNESS, str(pidfile), TREE_CHILD, str(env.home)],
        env=dict(os.environ, PYTHONPATH=SOURCE_ROOT, DBOUND_HOME=str(env.store.path.parent)),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, preexec_fn=None if IS_WINDOWS else _default_hangup_dispositions)
    try:
        info = wait_for_pidfile(pidfile)
        env.tracked += [info["child"], info["grandchild"], service.pid]
        service.stdout.close()
        os.kill(service.pid, signal.SIGHUP)
        _, errors = service.communicate(timeout=30)
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=15)
    assert service.returncode == 0, errors.decode()[-500:]
    assert errors == b""
    assert wait_dead([info["child"], info["grandchild"]], timeout=5) == []
    assert not LocalCoordinatorLeader(env.store).status()["running"]


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX pipes")
def test_summary_on_a_dead_pipe_leaves_nothing_to_fail_at_interpreter_exit():
    """A failed summary write must point stdout at the null device so nothing fails again at exit with status 120."""
    code = (
        "import sys\n"
        "from decisionbound import cli\n"
        "def broken(value):\n"
        "    sys.stdout.write('x' * 100)\n"
        "    sys.stdout.flush()\n"
        "cli._print = broken\n"
        "sys.stdin.read(1)\n"
        "cli._print_final_summary({'stopped': True})\n"
        "import os\n"
        "same = os.fstat(1).st_rdev == os.stat(os.devnull).st_rdev\n"
        "sys.stderr.write('discarded' if same else 'still-attached')\n")
    child = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=SOURCE_ROOT),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        child.stdout.close()
        _, errors = child.communicate(input=b"go", timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=15)
    assert child.returncode == 0, errors.decode()[-500:]
    assert errors == b"discarded", errors.decode()[-500:]


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX signals")
def test_a_handler_installed_from_c_is_restored_to_the_default(monkeypatch, tmp_path, default_hangup_dispositions):
    """getsignal returns None for such a handler, and signal.signal rejects None."""
    real = signal.getsignal
    monkeypatch.setattr(signal, "getsignal", lambda n: None if n == signal.SIGHUP else real(n))
    run_serve_in_process(monkeypatch, tmp_path, lambda stop_event: None)
    monkeypatch.undo()
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGQUIT) == signal.SIG_DFL


@pytest.mark.parametrize("code,kept", [(errno.EPIPE, False), (errno.EIO, False), (errno.EBADF, False), (errno.ENOSPC, True)])
def test_only_a_gone_output_is_forgiven_after_the_stop(monkeypatch, code, kept):
    """A full disk still fails, because the caller would otherwise never see the missing summary."""
    from decisionbound import cli

    def broken(value):
        raise OSError(code, "write failed")

    monkeypatch.setattr(cli, "_print", broken)
    monkeypatch.setattr(cli, "_discard_stdout", lambda: None)
    if kept:
        with pytest.raises(OSError):
            cli._print_final_summary({"stopped": True})
    else:
        cli._print_final_summary({"stopped": True})


def run_serve_in_process(monkeypatch, tmp_path, observe):
    """Run the serve command in this thread with a fake coordinator run.

    ``observe`` receives the stop event and runs inside the service loop.
    """
    from typer.testing import CliRunner
    from decisionbound import background, cli
    store = SQLiteRepository(tmp_path / "data" / "state.sqlite3")
    home = tmp_path / "project"
    home.mkdir()
    init_project(home, store)
    monkeypatch.setenv("DBOUND_HOME", str(store.path.parent))

    def fake_run(self, *, stop_event=None, **_):
        observe(stop_event)
        return {"stopped": True}

    monkeypatch.setattr(background.BackgroundCoordinator, "run", fake_run)
    outcome = CliRunner().invoke(cli.app, ["workflow", "serve", "--path", str(home)])
    assert outcome.exit_code == 0, outcome.output
    return outcome


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX signals")
def test_serve_installs_and_restores_every_stop_signal_handler(monkeypatch, tmp_path, default_hangup_dispositions):
    names = ["SIGINT", "SIGTERM"] + HANGUP_SIGNALS
    before = {n: signal.getsignal(getattr(signal, n)) for n in names}
    inside = {}

    def observe(stop_event):
        for n in names:
            inside[n] = signal.getsignal(getattr(signal, n))
        # Never raise a signal at this process unless a handler would catch it.
        assert callable(inside["SIGHUP"]), "SIGHUP has no handler"
        os.kill(os.getpid(), signal.SIGHUP)
        assert stop_event.wait(5), "SIGHUP did not set the stop event"

    run_serve_in_process(monkeypatch, tmp_path, observe)
    for n in names:
        assert inside[n] is not before[n], f"{n} handler was not installed"
        assert signal.getsignal(getattr(signal, n)) is before[n], f"{n} handler was not restored"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX signals")
@pytest.mark.parametrize("name", HANGUP_SIGNALS)
def test_serve_leaves_an_ignored_hangup_or_quit_ignored(monkeypatch, tmp_path, name):
    """`nohup` sets SIGHUP to ignore on purpose. Serve must not undo that."""
    number = getattr(signal, name)
    previous = signal.signal(number, signal.SIG_IGN)
    seen = {}
    try:
        run_serve_in_process(monkeypatch, tmp_path,
                             lambda stop_event: seen.update(inside=signal.getsignal(number)))
        assert seen["inside"] == signal.SIG_IGN
        assert signal.getsignal(number) == signal.SIG_IGN
    finally:
        signal.signal(number, previous)


def test_serve_runs_on_a_platform_without_hangup_and_quit_signals(monkeypatch, tmp_path, default_hangup_dispositions):
    for name in ("SIGHUP", "SIGQUIT"):
        monkeypatch.delattr(signal, name, raising=False)
    seen = {}

    def observe(stop_event):
        seen["term"] = signal.getsignal(signal.SIGTERM)

    before = signal.getsignal(signal.SIGTERM)
    run_serve_in_process(monkeypatch, tmp_path, observe)
    assert seen["term"] is not before  # the handlers that exist everywhere are still installed
    assert signal.getsignal(signal.SIGTERM) is before
