"""Paid-call reservations, crash ambiguity, and concurrent journal writes."""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor
import pytest

from decisionbound.code_journal import SQLiteRepairJournal, RepairConflict
from decisionbound.code_repair import ModelRepairFactory, RepairLimits, run_repair_session
from decisionbound.model_planner import ModelCompletion, Pricing
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.code_acceptance import TaskContract, TextPredicate
from tests.integration.test_code_workflow import project, PATCH


def contract():
    return TaskContract('budget-contract', ('app.py',), ('app.py',),
            (('python', '-m', 'pytest'),), (TextPredicate('app.py', contains='VALUE = 2'),))


def execute(project, factory, *, key='budget_repair_key_001', attempts=2, resume=False):
    root, state, db = project
    return run_repair_session(idempotency_key=key, project_path=root, state_root=state,
        repository=db, task='Change VALUE to 2', contract=contract(),
        proposer_factory=factory, runner=ScriptedTestRunner([1,0]),
        limits=RepairLimits(max_attempts=attempts, max_model_cost_usd=0.2),
        source_targets=('app.py',), approved_writes=True, resume=resume)


def test_successful_paid_calls_charge_usage_not_full_reservation(project):
    class Model:
        def __init__(self): self.messages=[]
        def complete(self, **kwargs):
            self.messages.append(kwargs['messages'])
            return ModelCompletion(json.dumps({'patch': PATCH, 'summary': 'small'}), 100, 200)
    model = Model()
    factory = ModelRepairFactory(model, Pricing(1,1))
    state = execute(project, factory)
    assert state.status == 'needs_real_tests'
    assert len(model.messages) == 2
    assert model.messages[0] != model.messages[1]
    assert 'Prior attempt' in model.messages[1][-1]['content']
    assert abs(state.cost_usd - 0.0006) < 1e-8
    assert len(state.attempts) == 2


def test_unknown_cost_on_process_termination_remains_reserved(project):
    class SystemExitModel:
        def complete(self, **kwargs):
            raise SystemExit('simulated process termination after network request')
    with pytest.raises(SystemExit):
        execute(project, ModelRepairFactory(SystemExitModel(), Pricing(1,1)))
    from decisionbound.storage import SQLiteRepository
    state = project[1]
    repo = project[2]
    with repo._connection() as db:
        row = db.execute("SELECT * FROM repair_sessions WHERE idempotency_key='budget_repair_key_001'").fetchone()
        assert row['cost_usd'] == 0.2
        attempt = db.execute('SELECT * FROM repair_attempts WHERE session_id=?', (row['id'],)).fetchone()
        assert attempt['status'] == 'in_flight'
    with pytest.raises(RepairConflict, match='manual reconciliation'):
        execute(project, ModelRepairFactory(SystemExitModel(), Pricing(1,1)), resume=True)


def test_concurrent_claims_only_one_succeeds(project):
    journal = SQLiteRepairJournal(project[2], project[1])
    state, _ = journal.open(idempotency_key='concurrent-key-001',
        request_sha256='a'*64, project_id='fixture-project', contract_sha256='b'*64,
        max_attempts=2)
    def reserve(_):
        try:
            return journal.reserve(state.session_id)
        except RepairConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len([x for x in results if x is not None]) == 1
    assert len(journal.get(state.session_id).attempts) == 1


def test_existing_repair_idempotency_cannot_change_cost_cap(project):
    model = type('Model', (), {'complete': lambda self, **kwargs: ModelCompletion(
        json.dumps({'patch': PATCH, 'summary': 'OK'}), 100, 100)})()
    execute(project, ModelRepairFactory(model, Pricing(1,1)), attempts=1)
    with pytest.raises(RepairConflict, match='another request'):
        root, state, db = project
        run_repair_session(idempotency_key='budget_repair_key_001', project_path=root,
            state_root=state, repository=db, task='Change VALUE to 2', contract=contract(),
            proposer_factory=ModelRepairFactory(model, Pricing(1,1)), runner=ScriptedTestRunner([0]),
            limits=RepairLimits(max_attempts=1, max_model_cost_usd=0.1),
            source_targets=('app.py',), approved_writes=True)
