"""Real-SQLite local daemon tests: fencing, process loss and resource controls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from decisionbound.background import (
    BackgroundCoordinator, CoordinatorConflict, CoordinatorSettings,
    LocalCoordinatorLeader, ResourceBudgetExceeded,
)
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import (
    PreparationWorker, ScriptedWorkflowWorker, WorkflowJournal, WorkflowSpec,
    WorkflowSupervisor, WorkflowTask,
)


@pytest.fixture
def environment(tmp_path):
    store = SQLiteRepository(tmp_path / 'data' / 'state.sqlite3')
    home = tmp_path / 'project'
    home.mkdir()
    project = init_project(home, store)
    journal = WorkflowJournal(store)
    return store, home, project, journal


def create(environment, *, kind='prepare', attempts=1, key='background_run'):
    _, home, _, journal = environment
    spec = WorkflowSpec((WorkflowTask('stage', kind, 'Inspect repository security boundaries',
                                     max_attempts=attempts),), max_workers=1)
    return journal.create(project_path=home, spec=spec, idempotency_key=key)


def coordinator(environment, *, workers=None, **options):
    store, _, project, _ = environment
    settings = CoordinatorSettings(**options)
    return BackgroundCoordinator(store, project_id=project.project_id,
                                 settings=settings, workers=workers)


def test_leadership_exclusive_and_fenced_after_crash(environment):
    store, _, _, _ = environment
    first = LocalCoordinatorLeader(store)
    other = LocalCoordinatorLeader(store)
    original = first.acquire(now=100, ttl_seconds=12)
    assert first.status(now=101)['running']
    assert 'owner_token' not in first.status(now=101)
    with pytest.raises(CoordinatorConflict):
        other.acquire(now=105, ttl_seconds=12)
    renewed = other.acquire(now=113, ttl_seconds=12)
    assert renewed.generation == original.generation + 1
    with pytest.raises(CoordinatorConflict):
        first.renew(original, now=114, ttl_seconds=12)
    assert first.release(original) is False
    other.renew(renewed, now=115, ttl_seconds=12)
    assert other.status(now=116)['running']
    assert other.release(renewed)
    assert not other.status(now=117)['running']


def test_daemon_once_completes_verified_preparation_without_model_calls(environment):
    wid = create(environment)
    result = coordinator(environment, max_rss_mib=None).run(once=True)
    assert result['cycles'] == 1 and result['task_completed'] is False
    assert environment[3].status(wid)['state'] == 'verified_stages'
    assert coordinator(environment).leader.status()['running'] is False
    assert environment[3].list_active(project_id=environment[2].project_id) == []


def test_restart_recovers_expired_idempotent_readonly_claim(environment):
    wid = create(environment, attempts=2)
    journal = environment[3]
    old = journal.claim(wid, worker_kinds={'prepare'}, now=time.time() - 50,
                        lease_seconds=5)
    assert old is not None
    result = coordinator(environment, max_rss_mib=None).run(once=True)
    assert result['cycles'] == 1
    assert journal.status(wid)['state'] == 'verified_stages'
    assert journal.status(wid)['tasks'][0]['attempt'] == 2
    assert any(x['event'] == 'lease_reconciled' for x in journal.events(wid))


def test_stale_write_claim_quarantined_instead_of_replayed(environment):
    wid = create(environment, kind='code')
    journal = environment[3]
    original = journal.claim(wid, worker_kinds={'code'}, now=time.time() - 50,
                             lease_seconds=5)
    assert original is not None
    # Default daemon is read-only; it cannot execute even an approved-looking
    # write task, and an ambiguous write must never run twice after a crash.
    coordinator(environment, max_rss_mib=None).run(once=True)
    assert journal.status(wid)['state'] == 'blocked'
    assert journal.status(wid)['tasks'][0]['error_type'] == 'unknown_write_outcome'
    assert len([e for e in journal.events(wid) if e['event'] == 'claimed']) == 1


def test_simultaneous_coordinator_cannot_take_over_active_leader(environment):
    wid = create(environment, kind='mock')
    started = threading.Event()

    class SignalledMock(ScriptedWorkflowWorker):
        def execute(self, assignment):
            started.set()
            time.sleep(0.25)
            return super().execute(assignment)

    first = coordinator(environment, workers={'mock': SignalledMock()},
                        max_rss_mib=None, poll_seconds=0.05)
    second = coordinator(environment, workers={'mock': ScriptedWorkflowWorker()},
                         max_rss_mib=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.run, once=True)
        assert started.wait(timeout=3)
        with pytest.raises(CoordinatorConflict):
            second.run(once=True)
        assert future.result()['cycles'] == 1
    assert environment[3].status(wid)['state'] == 'needs_real_tests'
    assert second.run(once=True)['cycles'] == 1


def test_project_scoped_queue_prevents_cross_project_execution(environment, tmp_path):
    _, home, project, journal = environment
    other = tmp_path / 'other-project'
    other.mkdir()
    foreign = init_project(other, environment[0])
    wid = journal.create(project_path=other,
        spec=WorkflowSpec((WorkflowTask('foreign', 'prepare', 'Examine this repository'),)),
        idempotency_key='foreign_workflow')
    assert journal.list_active(project_id=project.project_id) == []
    assert journal.list_active(project_id=foreign.project_id) == [wid]
    coordinator(environment, max_rss_mib=None).run(once=True)
    assert journal.status(wid)['tasks'][0]['state'] == 'pending'


def test_resource_cap_fails_closed_and_does_not_claim_task(environment, monkeypatch):
    wid = create(environment)
    monkeypatch.setattr('decisionbound.background.resident_memory_mib', lambda: 10000)
    with pytest.raises(ResourceBudgetExceeded):
        coordinator(environment, max_rss_mib=32).run(once=True)
    assert environment[3].status(wid)['tasks'][0]['state'] == 'pending'
    assert not coordinator(environment).leader.status()['running']


def test_missing_rss_measurement_fails_closed(environment, monkeypatch):
    create(environment)
    monkeypatch.setattr('decisionbound.background.resident_memory_mib',
                        lambda: (_ for _ in ()).throw(RuntimeError('unavailable')))
    with pytest.raises(ResourceBudgetExceeded):
        coordinator(environment).run(once=True)


def test_stop_event_prevents_dispatch(environment):
    wid = create(environment)
    flag = threading.Event()
    flag.set()
    result = coordinator(environment, max_rss_mib=None).run(stop_event=flag, once=True)
    assert result['cycles'] == 0
    assert environment[3].status(wid)['tasks'][0]['state'] == 'pending'


def test_lease_heartbeats_protect_a_real_slow_local_worker(environment):
    wid = create(environment, kind='mock')
    journal = environment[3]
    # The task exceeds the five-second lease. Without heartbeat renewal it
    # would expire and lose its completion right before returning.
    worker = ScriptedWorkflowWorker(delay=5.5)
    result = WorkflowSupervisor(journal, workers={'mock': worker}).run(
        wid, lease_seconds=5, max_wall_seconds=10)
    assert result['state'] == 'needs_real_tests'
    assert journal.status(wid)['tasks'][0]['attempt'] == 1


def test_configuration_rejects_unbounded_resource_or_poll_settings():
    for arguments in ({'poll_seconds': 0}, {'poll_seconds': 200},
                      {'leader_lease_seconds': 1}, {'max_rss_mib': 0},
                      {'max_workflows_per_cycle': 10000},
                      {'max_dispatch_per_workflow': 0},
                      {'max_wall_seconds_per_workflow': 1}):
        with pytest.raises(ValueError):
            CoordinatorSettings(**arguments)


def test_real_process_crash_then_cli_restart_recovers_only_safe_work(environment, tmp_path):
    """An os._exit worker disappears without finally blocks; new CLI resumes."""
    import os
    import subprocess
    import sys

    store, home, project, journal = environment
    wid = create(environment, attempts=2)
    script = '''import os, time
from pathlib import Path
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import WorkflowJournal
store = SQLiteRepository(Path(os.environ['DBOUND_HOME']) / 'state.sqlite3')
lease = WorkflowJournal(store).claim(os.environ['DBOUND_WORKFLOW'], worker_kinds={'prepare'},
                                    now=time.time()-100, lease_seconds=5)
assert lease is not None
os._exit(37)
'''
    source_root = str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'src')
    env = dict(os.environ, DBOUND_HOME=str(store.path.parent), DBOUND_WORKFLOW=wid,
               PYTHONPATH=source_root)
    crashed = subprocess.run([sys.executable, '-c', script], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    assert crashed.returncode == 37, crashed.stderr.decode()
    assert journal.status(wid)['tasks'][0]['state'] == 'leased'
    resumed = subprocess.run([sys.executable, '-m', 'decisionbound.cli', 'workflow', 'serve',
                              '--once', '--path', str(home)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert resumed.returncode == 0, resumed.stderr.decode()
    import json
    assert json.loads(resumed.stdout)['task_completed'] is False
    assert journal.status(wid)['state'] == 'verified_stages'
    assert journal.status(wid)['tasks'][0]['attempt'] == 2


def test_cli_leadership_status_has_no_secret_token(environment, monkeypatch):
    import os
    import subprocess
    import sys
    store, home, _, _ = environment
    env = dict(os.environ, DBOUND_HOME=str(store.path.parent),
               PYTHONPATH=str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'src'))
    completed = subprocess.run([sys.executable, '-m', 'decisionbound.cli', 'workflow',
                                'leader-status'], env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    assert 'owner_token' not in completed.stdout
    assert 'running' in completed.stdout


def test_keyset_rotation_does_not_starve_new_workflows(environment):
    _, home, project, journal = environment
    ids = [journal.create(project_path=home,
            spec=WorkflowSpec((WorkflowTask('stage', 'prepare', 'Inspect repository conventions'),)),
            idempotency_key=f'rotation_job_{i}') for i in range(7)]
    ordered = journal.list_active(project_id=project.project_id, limit=7)
    first_batch = journal.list_active(project_id=project.project_id, limit=3)
    next_batch = journal.list_active(project_id=project.project_id, limit=3, after=first_batch[-1])
    assert len(set(first_batch + next_batch)) == 6
    assert set(first_batch + next_batch) <= set(ordered)
    # Run until every workflow is served instead of for a fixed two seconds: a
    # slow disk makes each cycle slower, and starvation shows as never finishing.
    stop = threading.Event()
    service = coordinator(environment, max_rss_mib=None,
                          max_workflows_per_cycle=2, poll_seconds=0.05)
    result: dict = {}
    runner = threading.Thread(target=lambda: result.update(
        service.run(stop_event=stop, max_seconds=120)))
    runner.start()
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline and not all(
                journal.status(wid)['state'] == 'verified_stages' for wid in ids):
            time.sleep(0.05)
    finally:
        stop.set()
        runner.join(30)
    assert not runner.is_alive()
    assert result['cycles'] >= 4
    assert all(journal.status(wid)['state'] == 'verified_stages' for wid in ids)


def test_unregistered_project_cannot_construct_background_runner(environment):
    store = environment[0]
    with pytest.raises(PermissionError):
        BackgroundCoordinator(store, project_id='prj_' + 'f' * 32)


def test_repeated_short_cycles_do_not_leak_heartbeat_threads(environment):
    service = coordinator(environment, max_rss_mib=None)
    for _ in range(10):
        assert service.run(once=True)['stopped']
    assert not any(thread.name == 'dbound-leader-heartbeat' and thread.is_alive()
                   for thread in threading.enumerate())


def test_low_resource_cli_uses_safe_offline_worker_defaults(environment):
    """Exercise the actual installed-style command without model or shell access."""
    import json
    import os
    import subprocess
    import sys
    store, home, _, journal = environment
    workflow = create(environment)
    environment_variables = dict(os.environ, DBOUND_HOME=str(store.path.parent),
        PYTHONPATH=str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'src'))
    result = subprocess.run([sys.executable, '-m', 'decisionbound.cli', 'workflow',
                             'serve', '--once', '--low-resource', '--path', str(home)],
                            env=environment_variables, text=True, capture_output=True,
                            timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['task_completed'] is False
    assert journal.status(workflow)['state'] == 'verified_stages'
