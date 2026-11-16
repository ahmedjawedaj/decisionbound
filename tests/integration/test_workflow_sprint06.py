"""Sprint 06 integration and adversarial recovery tests (offline, no providers)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import (
    CodingRepairWorker, IndependentTaskVerifier, PreparationWorker,
    ScriptedWorkflowWorker, TaskAssignment, WorkerReceipt, WorkflowConflict,
    WorkflowJournal, WorkflowSpec, WorkflowSupervisor, WorkflowTask,
    WorkflowWorker,
)


@pytest.fixture
def setup(tmp_path):
    repo = SQLiteRepository(tmp_path / 'private' / 'state.sqlite3')
    project = tmp_path / 'repo'
    project.mkdir()
    init_project(project, repo)
    journal = WorkflowJournal(repo)
    return repo, project, journal


def task(task_id, kind='prepare', depends=(), attempts=1, cost=0.0):
    return WorkflowTask(task_id, kind, 'Inspect repository conventions', tuple(depends), attempts, cost)


def submit(setup, tasks, *, budget=0, max_workers=2, key='workflow_test'):
    repo, project, journal = setup
    spec = WorkflowSpec(tuple(tasks), max_workers, budget)
    return journal.create(project_path=project, spec=spec, idempotency_key=key)


def task_states(journal, wid):
    return {t['task_id']: t['state'] for t in journal.status(wid)['tasks']}


def test_parallel_dependencies_and_independent_proofs(setup):
    repo, project, journal = setup
    wf = submit(setup, [task('one'), task('two'), task('three', depends=['one', 'two'])], max_workers=2)
    result = WorkflowSupervisor(journal).run(wf)
    assert result['state'] == 'verified_stages'
    assert result['task_completed'] is False
    assert task_states(journal, wf) == dict(one='verified', two='verified', three='verified')
    claims = [(e['task_id'], e['event']) for e in journal.events(wf) if e['event'] == 'claimed']
    assert len(claims) == 3 and set(t for t, _ in claims) == {'one', 'two', 'three'}


def test_mock_is_explicit_and_does_not_unlock_real_work(setup):
    wf = submit(setup, [task('fake', 'mock'), task('real', depends=['fake'])])
    result = WorkflowSupervisor(setup[2]).run(wf)
    assert result['state'] == 'blocked'
    assert task_states(setup[2], wf) == {'fake': 'mock_passed', 'real': 'blocked'}
    assert not result['task_completed']


def test_mock_only_results_never_report_task_success(setup):
    wf = submit(setup, [task('fake', 'mock')])
    assert WorkflowSupervisor(setup[2]).run(wf)['state'] == 'needs_real_tests'


def test_two_supervisors_claim_each_task_only_once(setup):
    wf = submit(setup, [task(f'node_{i}', 'mock') for i in range(12)], max_workers=3)
    worker = ScriptedWorkflowWorker(delay=0.015)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(WorkflowSupervisor(setup[2], workers={'mock': worker}).run, wf)
                   for _ in range(2)]
        for future in futures:
            future.result()
    claims = [e for e in setup[2].events(wf) if e['event'] == 'claimed']
    assert len(claims) == 12
    assert len({c['task_id'] for c in claims}) == 12
    assert setup[2].status(wf)['state'] == 'needs_real_tests'


def test_expired_readonly_claim_retries_and_rejects_late_worker(setup):
    wf = submit(setup, [task('read', attempts=2)])
    journal = setup[2]
    original = journal.claim(wf, worker_kinds={'prepare'}, now=100, lease_seconds=10)
    journal.reconcile(wf, now=111)
    renewed = journal.claim(wf, worker_kinds={'prepare'}, now=112, lease_seconds=10)
    assert renewed.attempt == 2 and renewed.generation != original.generation
    with pytest.raises(WorkflowConflict):
        journal.finish(original, state='verified', receipt=WorkerReceipt('prepared'), now=113)
    completion = PreparationWorker(setup[0]).execute(renewed.assignment())
    assert IndependentTaskVerifier(setup[0]).verify(renewed, completion) == 'verified'
    journal.finish(renewed, state='verified', receipt=completion, now=113)
    assert journal.status(wf)['state'] == 'verified_stages'


def test_expired_code_lease_never_replayed(setup):
    journal = setup[2]
    wf = submit(setup, [task('code_task', 'code')])
    first = journal.claim(wf, worker_kinds={'code'}, now=100, lease_seconds=10)
    journal.reconcile(wf, now=111)
    assert task_states(journal, wf)['code_task'] == 'blocked'
    assert journal.claim(wf, worker_kinds={'code'}, now=112) is None
    with pytest.raises(WorkflowConflict):
        journal.finish(first, state='verified', receipt=WorkerReceipt('code_result'), now=113)
    assert any(e['metadata'].get('reason') == 'unknown_write_outcome' for e in journal.events(wf))


def test_budget_reservations_block_extra_work(setup):
    journal = setup[2]
    wf = submit(setup, [task('one', cost=.1), task('two', cost=.1)], budget=.15, max_workers=1)
    a = journal.claim(wf, worker_kinds={'prepare'})
    receipt = PreparationWorker(setup[0]).execute(a.assignment())
    journal.finish(a, state='verified', receipt=receipt)
    assert journal.claim(wf, worker_kinds={'prepare'}) is None
    assert task_states(journal, wf) == {'one': 'verified', 'two': 'blocked'}
    assert journal.status(wf)['reserved_cost_usd'] == pytest.approx(.1)


def test_cancel_revokes_lease_and_does_not_kill_process(setup):
    journal = setup[2]
    wf = submit(setup, [task('first'), task('second')])
    lease = journal.claim(wf, worker_kinds={'prepare'})
    journal.cancel(wf)
    assert all(x == 'cancelled' for x in task_states(journal, wf).values())
    with pytest.raises(WorkflowConflict):
        journal.finish(lease, state='verified', receipt=WorkerReceipt('prepared'))


def test_idempotency_accepts_exact_same_request_and_rejects_mismatch(setup):
    journal = setup[2]
    one = submit(setup, [task('alpha')], key='same_request')
    assert submit(setup, [task('alpha')], key='same_request') == one
    with pytest.raises(WorkflowConflict):
        submit(setup, [task('beta')], key='same_request')
    assert len([e for e in journal.events(one) if e['event'] == 'created']) == 1


def test_prepare_worker_forged_receipt_is_blocked(setup):
    class Forger(WorkflowWorker):
        def execute(self, lease: TaskAssignment) -> WorkerReceipt:
            return WorkerReceipt('prepared', evidence_sha256='0' * 64)
    journal = setup[2]
    wf = submit(setup, [task('inspect')])
    outcome = WorkflowSupervisor(journal, workers={'prepare': Forger()}).run(wf)
    assert outcome['state'] == 'blocked'
    assert task_states(journal, wf)['inspect'] == 'blocked'


def test_code_cannot_forge_verification_with_fake_session(setup):
    class Fabricator(WorkflowWorker):
        def execute(self, lease: TaskAssignment) -> WorkerReceipt:
            assert not hasattr(lease, 'owner_token')
            return WorkerReceipt('code_result', repair_session_id='fake-session')
    journal = setup[2]
    wf = submit(setup, [task('patch', 'code')])
    outcome = WorkflowSupervisor(journal, workers={'code': Fabricator()},
                                 verifier=IndependentTaskVerifier(setup[0], setup[1].parent)).run(wf)
    assert outcome['state'] == 'blocked'


def test_missing_worker_does_not_spawn_or_block(setup):
    journal = setup[2]
    wf = submit(setup, [task('patch', 'code')])
    assert WorkflowSupervisor(journal).run(wf)['state'] == 'running'
    assert task_states(journal, wf)['patch'] == 'pending'


def test_readonly_exception_retries_when_allowed(setup):
    class Once(WorkflowWorker):
        def __init__(self):
            self.n = 0
        def execute(self, lease):
            self.n += 1
            if self.n == 1:
                raise RuntimeError('transient')
            return PreparationWorker(setup[0]).execute(lease)
    journal = setup[2]
    wf = submit(setup, [task('p', attempts=2)])
    one = Once()
    assert WorkflowSupervisor(journal, workers={'prepare': one}).run(wf)['state'] == 'verified_stages'
    assert one.n == 2
    assert len([e for e in journal.events(wf) if e['event'] == 'failed_attempt']) == 1


def test_failed_dependency_cascades_through_three_levels(setup):
    journal = setup[2]
    wf = submit(setup, [task('a', 'mock'), task('b', depends=['a']), task('c', depends=['b'])])
    WorkflowSupervisor(journal).run(wf)
    assert task_states(journal, wf) == {'a': 'mock_passed', 'b': 'blocked', 'c': 'blocked'}


def test_invalid_dags_and_fields_rejected():
    with pytest.raises(ValueError, match='Cyclic'):
        WorkflowSpec((task('a', depends=['b']), task('b', depends=['a'])))
    with pytest.raises(ValueError, match='Unknown'):
        WorkflowSpec((task('a', depends=['missing']),))
    with pytest.raises(ValueError, match='duplicate'):
        WorkflowSpec((task('a'), task('a')))
    with pytest.raises(ValueError):
        WorkflowTask('x', 'code', 'patch', max_attempts=2)
    with pytest.raises(ValueError):
        WorkflowSpec.from_dict({'schema_version':1,'tasks':[], 'arbitrary':'no'})
    with pytest.raises(ValueError):
        WorkflowSpec.from_dict({'schema_version':1,'tasks':[{'task_id':'a','kind':'mock',
                                                            'description':'x','depends_on':'string'}]})
    with pytest.raises(ValueError):
        WorkflowSpec.from_dict({'schema_version':1,'tasks':[{'task_id':'a','kind':'mock',
                                                            'description':'x','max_attempts':True}]})


def test_private_task_text_not_in_event_ledger(setup):
    secret = 'PRIVATE_INTERNAL_SOURCE_123456'
    journal = setup[2]
    spec = WorkflowSpec((WorkflowTask('job', 'prepare', secret),))
    wf = journal.create(project_path=setup[1], spec=spec, idempotency_key='private_work')
    WorkflowSupervisor(journal).run(wf)
    assert secret not in json.dumps(journal.events(wf))
    assert secret not in json.dumps(journal.status(wf))


def test_lease_heartbeat_validates_token(setup):
    journal = setup[2]
    wf = submit(setup, [task('a')])
    lease = journal.claim(wf, worker_kinds={'prepare'})
    journal.heartbeat(lease, lease_seconds=60)
    fake = type(lease)(lease.workflow_id, lease.project_id, lease.project_path, lease.task,
                       lease.attempt, lease.generation, 'wrong-token', lease.reserved_cost_usd)
    with pytest.raises(WorkflowConflict):
        journal.heartbeat(fake)


def test_source_project_changes_block_preparation(setup):
    journal = setup[2]
    wf = submit(setup, [task('p')])
    lease = journal.claim(wf, worker_kinds={'prepare'})
    (setup[1] / '.decisionbound' / 'project.yaml').unlink()
    with pytest.raises(FileNotFoundError):
        PreparationWorker(setup[0]).execute(lease.assignment())
    journal.fail(lease, FileNotFoundError())
    assert journal.status(wf)['state'] == 'blocked'


def test_worker_cost_exceeding_reservation_is_rejected(setup):
    journal = setup[2]
    wf = submit(setup, [task('p', cost=.01)], budget=.01)
    lease = journal.claim(wf, worker_kinds={'prepare'})
    with pytest.raises(WorkflowConflict):
        journal.finish(lease, state='verified', receipt=WorkerReceipt('prepared', cost_usd=.02))
    assert journal.status(wf)['tasks'][0]['state'] == 'leased'


def test_verified_handoff_contains_only_trusted_fingerprints(setup):
    class Capture(WorkflowWorker):
        def __init__(self):
            self.handoffs = None
        def execute(self, lease):
            self.handoffs = lease.handoffs
            return PreparationWorker(setup[0]).execute(lease)
    journal = setup[2]
    wf = submit(setup, [task('parent'), task('child', depends=['parent'])])
    recorder = Capture()
    assert WorkflowSupervisor(journal, workers={'prepare': recorder}).run(wf)['state'] == 'verified_stages'
    assert recorder.handoffs[0].task_id == 'parent'
    assert recorder.handoffs[0].kind == 'prepare'
    assert recorder.handoffs[0].verified is True
    assert len(recorder.handoffs[0].receipt_sha256) == 64
    assert not hasattr(recorder.handoffs[0], 'owner_token')


def test_role_specific_routing_selects_correct_agent(setup):
    class Reviewer(WorkflowWorker):
        def execute(self, lease):
            assert lease.task.role == 'reviewer'
            return PreparationWorker(setup[0]).execute(lease)
    journal = setup[2]
    wf = submit(setup, [WorkflowTask('review', 'prepare', 'Inspect safe design', role='reviewer')])
    assert WorkflowSupervisor(journal, workers={'prepare:reviewer': Reviewer()}).run(wf)['state'] == 'verified_stages'
    assert journal.events(wf)[1]['metadata']['role'] == 'reviewer'


def test_expired_paid_readonly_task_is_not_retried(setup):
    journal = setup[2]
    wf = submit(setup, [task('paid', attempts=2, cost=.2)], budget=.5)
    journal.claim(wf, worker_kinds={'prepare'}, now=100, lease_seconds=10)
    assert journal.reconcile(wf, now=111)['state'] == 'blocked'
    assert task_states(journal, wf)['paid'] == 'failed'


def test_over_budget_worker_receipt_quarantined_by_supervisor(setup):
    class Expensive(WorkflowWorker):
        def execute(self, lease):
            return WorkerReceipt('prepared', cost_usd=99)
    journal = setup[2]
    wf = submit(setup, [task('budget', cost=.01)], budget=.01)
    result = WorkflowSupervisor(journal, workers={'prepare': Expensive()}).run(wf)
    assert result['state'] == 'blocked'
    assert result['tasks'][0]['error_type'] == 'over_budget'
    assert len([e for e in journal.events(wf) if e['event'] == 'quarantined']) == 1


def test_code_repair_adapter_mock_proof_stays_unverified(tmp_path):
    from tests.integration.test_code_workflow import project as project_fixture, PATCH
    from decisionbound.code_acceptance import TaskContract, TextPredicate
    from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
    from decisionbound.code_sandbox import ScriptedTestRunner
    # Exercise the original test fixture's Git-backed worktree setup.
    root, state, repo = project_fixture.__wrapped__(tmp_path)
    journal = WorkflowJournal(repo)
    contract = TaskContract('work-acceptance', ('app.py',), ('app.py',),
                            (('python', '-m', 'pytest', '-q'),),
                            (TextPredicate('app.py', contains='VALUE = 2'),))
    spec = WorkflowSpec((WorkflowTask('change', 'code', 'Update the fixture to VALUE = 2'),))
    wf = journal.create(project_path=root, spec=spec, idempotency_key='code_workflow_fixture')
    worker = CodingRepairWorker(repository=repo, state_root=state, approved={'change': {
        'approved_writes': True, 'contract': contract,
        'factory': ScriptedRepairFactory((PATCH,)), 'runner': ScriptedTestRunner([0]),
        'limits': RepairLimits(max_attempts=1), 'source_targets': ('app.py',)}})
    result = WorkflowSupervisor(journal, workers={'code': worker},
                verifier=IndependentTaskVerifier(repo, state)).run(wf)
    assert result['state'] == 'needs_real_tests'
    assert result['tasks'][0]['state'] == 'needs_real_tests'
    assert (root / 'app.py').read_text() == 'VALUE = 1\n'


def test_code_repair_adapter_demands_budget_authority(tmp_path):
    from tests.integration.test_code_workflow import project as project_fixture, PATCH
    from decisionbound.code_acceptance import TaskContract, TextPredicate
    from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
    from decisionbound.code_sandbox import ScriptedTestRunner
    root, state, repo = project_fixture.__wrapped__(tmp_path)
    journal = WorkflowJournal(repo)
    wf = journal.create(project_path=root,
                        spec=WorkflowSpec((WorkflowTask('change', 'code', 'Update value'),)),
                        idempotency_key='code_budget_guard')
    con = TaskContract('write-contract', ('app.py',), ('app.py',),
                       (('python', '-m', 'pytest', '-q'),), (TextPredicate('app.py', contains='VALUE = 2'),))
    worker = CodingRepairWorker(repository=repo, state_root=state, approved={'change': {
        'approved_writes': True, 'contract': con,
        'factory': ScriptedRepairFactory((PATCH,)), 'runner': ScriptedTestRunner([0]),
        'limits': RepairLimits(max_attempts=1, max_model_cost_usd=1), 'source_targets': ('app.py',)}})
    result = WorkflowSupervisor(journal, workers={'code': worker},
        verifier=IndependentTaskVerifier(repo, state)).run(wf)
    assert result['state'] == 'blocked'
    assert any(e['metadata'].get('error_type') == 'PermissionError'
               for e in journal.events(wf) if e['event'] == 'failed_attempt')


def test_host_journal_rejects_fake_verified_completion_even_if_called_directly(setup):
    journal = setup[2]
    wf = submit(setup, [task('synthetic', 'mock')])
    lease = journal.claim(wf, worker_kinds={'mock'})
    with pytest.raises(WorkflowConflict, match='host gate'):
        journal.finish(lease, state='verified', receipt=WorkerReceipt('mock_passed'))
    assert journal.status(wf)['tasks'][0]['state'] == 'leased'
    journal.finish(lease, state='mock_passed', receipt=WorkerReceipt('mock_passed'))
    assert journal.status(wf)['state'] == 'needs_real_tests'


def test_host_journal_rejects_forged_prepare_receipt(setup):
    journal = setup[2]
    wf = submit(setup, [task('inspect')])
    lease = journal.claim(wf, worker_kinds={'prepare'})
    with pytest.raises(WorkflowConflict, match='host gate'):
        journal.finish(lease, state='verified', receipt=WorkerReceipt('prepared', '0'*64))
    assert journal.status(wf)['state'] == 'running'
    journal.quarantine(lease)
    assert journal.status(wf)['state'] == 'blocked'


def test_model_inspection_worker_integrates_existing_bounded_tool_loop(setup):
    from decisionbound.workflows import ModelInspectionWorker
    from decisionbound.model_planner import ScriptedModelTransport, Pricing
    root = setup[1]
    (root / 'service.py').write_text('def check_access():\n    return True\n')
    journal = setup[2]
    wf = submit(setup, [WorkflowTask('inspect', 'inspect', 'Find check_access')])

    def transport_factory(assignment):
        return ScriptedModelTransport([
            json.dumps({'kind':'tool', 'tool':'search_project',
                        'arguments_json': json.dumps({'query':'check_access'}), 'message':''}),
            json.dumps({'kind':'finish', 'tool':None, 'arguments_json':'{}',
                        'message':'Inspected repository evidence; task not independently completed.'}),
        ])
    worker = ModelInspectionWorker(repository=setup[0], transport_factory=transport_factory,
                                   pricing=Pricing(0,0))
    result = WorkflowSupervisor(journal, workers={'inspect':worker}).run(wf)
    assert result['state'] == 'verified_stages'
    assert result['task_completed'] is False
    nested = [e['metadata']['nested_run_id'] for e in journal.events(wf)
              if e['event']=='finished' and e['task_id']=='inspect'][0]
    scope = [e['payload'] for e in setup[0].events(nested) if e['event']=='scope']
    assert scope[0]['project_id'] == result['project_id']
    assert len([e for e in setup[0].events(nested) if e['event']=='tool_observation']) == 1


def test_replay_of_nested_model_evidence_into_another_workflow_rejected(setup):
    from decisionbound.workflows import ModelInspectionWorker
    from decisionbound.model_planner import ScriptedModelTransport, Pricing
    (setup[1] / 'a.py').write_text('def check_access(): pass\n')
    journal = setup[2]
    previous = submit(setup, [WorkflowTask('inspect', 'inspect', 'Find check_access')], key='first_model_workflow')
    def factory(_):
        return ScriptedModelTransport([
            json.dumps({'kind':'tool','tool':'search_project','arguments_json':'{"query":"check_access"}','message':''}),
            json.dumps({'kind':'finish','tool':None,'arguments_json':'{}','message':'Done inspecting'}),
        ])
    worker = ModelInspectionWorker(repository=setup[0], transport_factory=factory, pricing=Pricing(0,0))
    assert WorkflowSupervisor(journal, workers={'inspect':worker}).run(previous)['state']=='verified_stages'
    nested = next(e['metadata']['nested_run_id'] for e in journal.events(previous)
                  if e['event']=='finished' and e['task_id']=='inspect')
    other = submit(setup, [WorkflowTask('inspect', 'inspect', 'Find check_access')], key='second_model_workflow')
    class Replayer(WorkflowWorker):
        def execute(self, assignment):
            return WorkerReceipt('inspected', nested_run_id=nested)
    result = WorkflowSupervisor(journal, workers={'inspect':Replayer()}).run(other)
    assert result['state']=='blocked'


def test_paid_inspection_without_reserved_budget_never_calls_model(setup):
    from decisionbound.workflows import ModelInspectionWorker
    from decisionbound.model_planner import Pricing, ScriptedModelTransport
    journal = setup[2]
    wf = submit(setup, [WorkflowTask('inspect', 'inspect', 'Read repository')])
    class RecordingTransport(ScriptedModelTransport):
        def complete(self, **kwargs):
            raise AssertionError('Model provider should not be invoked')
    worker = ModelInspectionWorker(repository=setup[0],
        transport_factory=lambda _: RecordingTransport([]), pricing=Pricing(2,8))
    outcome = WorkflowSupervisor(journal, workers={'inspect':worker}).run(wf)
    assert outcome['state'] == 'blocked'
    assert journal.events(wf)[-1]['event']=='finished'


def test_concurrent_supervisors_stress_no_duplicate_claims_and_global_cap(setup):
    """Forty tasks over four competing supervisors, six shared concurrency slots."""
    journal = setup[2]
    wf = submit(setup, [task(f'job_{i}', 'mock') for i in range(40)],
                max_workers=6, key='stress_concurrent_local')

    class Measured(WorkflowWorker):
        def __init__(self):
            self.running = 0
            self.peak = 0
            self.calls = 0
            self.lock = threading.Lock()
        def execute(self, assignment):
            with self.lock:
                self.running += 1
                self.calls += 1
                self.peak = max(self.peak, self.running)
            time.sleep(.012)
            with self.lock:
                self.running -= 1
            return WorkerReceipt('mock_passed')

    worker = Measured()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(WorkflowSupervisor(journal, workers={'mock':worker}).run, wf)
                   for _ in range(4)]
        for future in results:
            future.result()
    assert worker.calls == 40
    assert 1 < worker.peak <= 6
    assert len([e for e in journal.events(wf) if e['event']=='claimed']) == 40
    assert journal.status(wf)['state']=='needs_real_tests'
