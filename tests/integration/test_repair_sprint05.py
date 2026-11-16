"""Crash, idempotency, repair feedback, and contract completion acceptance tests."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_execution import PatchFileProposer, run_coding_task
from decisionbound.code_journal import SQLiteRepairJournal, RepairConflict
from decisionbound.code_repair import (RepairLimits, ScriptedRepairFactory,
                                        run_repair_session)
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.code_workspace import GitWorktreeManager
from tests.integration.test_code_workflow import PATCH, ADD, project


def contract(*, expected='VALUE = 2', allowed=('app.py',), required=('app.py',)):
    return TaskContract('test-repair-acceptance', allowed, required,
                        (('python', '-m', 'pytest', '-q'),), (TextPredicate('app.py', contains=expected),))


def run(project, factory, runner, *, key='repair_example_001', limits=None, resume=False, target=None):
    root, state, db = project
    return run_repair_session(idempotency_key=key, project_path=root, state_root=state,
                              repository=db, task='Update the fixture to VALUE = 2',
                              contract=target or contract(), proposer_factory=factory, runner=runner,
                              limits=limits or RepairLimits(max_attempts=2),
                              source_targets=('app.py',), approved_writes=True, resume=resume)


def test_repair_failed_test_then_mock_pass_and_feedback(project):
    factory = ScriptedRepairFactory((PATCH, PATCH))
    runner = ScriptedTestRunner([1, 0])
    state = run(project, factory, runner)
    assert state.status == 'needs_real_tests'
    assert [a.status for a in state.attempts] == ['failed_test', 'mock_passed']
    assert factory.feedback_seen[0].failure_kind == ''
    assert factory.feedback_seen[1].failure_kind == 'failed_test'
    assert factory.feedback_seen[1].failed_test_indices == (0,)
    assert (project[0]/'app.py').read_text() == 'VALUE = 1\n'
    assert GitWorktreeManager(project[1]).pending() == []
    assert run(project, factory, runner).to_dict() == state.to_dict()  # no replay of paid/writes
    assert len(factory.feedback_seen) == 2
    assert 'VALUE = 2' not in json.dumps(state.to_dict())


def test_acceptance_contract_is_host_checked(project):
    result = run(project, ScriptedRepairFactory((PATCH,)), ScriptedTestRunner([0]),
                 limits=RepairLimits(max_attempts=1), target=contract(expected='VALUE = 999'))
    assert result.status == 'exhausted'
    assert result.attempts[0].status == 'failed_contract'
    assert result.attempts[0].summary['contract_verified'] is False


def test_patch_scope_outside_contract_is_blocked(project):
    change = ADD
    result = run(project, ScriptedRepairFactory((change,)), ScriptedTestRunner([0]))
    assert result.status == 'blocked'
    assert result.attempts[0].status == 'blocked'
    assert not (project[0]/'new.py').exists()
    assert GitWorktreeManager(project[1]).pending() == []


def test_new_request_with_existing_key_is_rejected(project):
    factory = ScriptedRepairFactory((PATCH,))
    run(project, factory, ScriptedTestRunner([0]), limits=RepairLimits(max_attempts=1))
    with pytest.raises(RepairConflict, match='another request'):
        run(project, factory, ScriptedTestRunner([0]),
            limits=RepairLimits(max_attempts=1), target=contract(expected='ANOTHER'))


def test_resume_completed_uncheckpointed_attempt_without_reexecution(project):
    root, state, db = project
    journal = SQLiteRepairJournal(db, state)
    contract_ = contract()
    from decisionbound.code_repair import _request_digest
    limits = RepairLimits(max_attempts=1)
    _, base = GitWorktreeManager.check_source(root)
    info = _request_digest(project_id=__import__('decisionbound.identity', fromlist=['load_project']).load_project(root, db).project_id,
                           base_commit=base, task='Update the fixture to VALUE = 2',
                           contract=contract_, sources=('app.py',), limits=limits, timeout_seconds=120)
    session, fresh = journal.open(idempotency_key='repair_example_001', request_sha256=info,
                                  project_id=__import__('decisionbound.identity', fromlist=['load_project']).load_project(root, db).project_id,
                                  contract_sha256=contract_.sha256, max_attempts=1)
    attempt = journal.reserve(session.session_id)
    outcome = run_coding_task(project_path=root, state_root=state, repository=db,
                              task='Update the fixture to VALUE = 2',
                              proposer=PatchFileProposer(PATCH), runner=ScriptedTestRunner([0]),
                              test_commands=contract_.test_commands, contract=contract_,
                              source_targets=('app.py',), approved_writes=True, run_id=attempt.run_id)
    assert not outcome.contract_verified
    # Simulate a process crash *after* code_finished but before journal completion.
    restored = run(project, ScriptedRepairFactory((ADD,)), ScriptedTestRunner([0]),
                   limits=limits, resume=True)
    assert restored.status == 'needs_real_tests'
    assert len(restored.attempts) == 1
    assert restored.attempts[0].summary['recovered_from_host_receipts'] is True


def test_ambiguous_inflight_cannot_auto_replay(project):
    root, state, db = project
    from decisionbound.code_repair import _request_digest
    from decisionbound.identity import load_project
    limits, con = RepairLimits(max_attempts=1), contract()
    _, base = GitWorktreeManager.check_source(root)
    journal = SQLiteRepairJournal(db, state)
    session, _ = journal.open(idempotency_key='repair_example_001',
            request_sha256=_request_digest(project_id=load_project(root, db).project_id, base_commit=base,
               task='Update the fixture to VALUE = 2', contract=con, sources=('app.py',),
               limits=limits, timeout_seconds=120), project_id=load_project(root, db).project_id,
               contract_sha256=con.sha256, max_attempts=1)
    attempt = journal.reserve(session.session_id)
    with pytest.raises(RepairConflict, match='manual reconciliation'):
        run(project, ScriptedRepairFactory((PATCH,)), ScriptedTestRunner([0]),
            limits=limits, resume=True)
    assert db.events(attempt.run_id) == []
    assert journal.get(session.session_id).status == 'running'


def test_invalid_contract_paths_and_test_changes_rejected(project):
    root, state, db = project
    with pytest.raises(ValueError):
        TaskContract('test', ('app.py',), ('other.py',), (('python', '-V'),),
                     (TextPredicate('app.py', contains='OK'),))
    blocked = TaskContract('test', ('.env',), ('.env',), (('python', '-V'),),
                           (TextPredicate('.env', contains='x'),))
    with pytest.raises(ValueError, match='Test commands differ'):
        blocked.preflight(root, __import__('decisionbound.code_patches', fromlist=['PatchPolicy']).PatchPolicy(),
                          (('python', '-m', 'pytest'),))
    with pytest.raises(Exception, match='Protected'):
        run(project, ScriptedRepairFactory((PATCH,)), ScriptedTestRunner([0]), target=blocked)
    with pytest.raises(ValueError, match='exactly'):
        TaskContract.from_dict({'contract_id': 'test'})


def test_schema_one_database_upgrade_to_two(tmp_path):
    import sqlite3
    from decisionbound.storage import SQLiteRepository
    dbfile = tmp_path / 'legacy.db'
    conn = sqlite3.connect(dbfile)
    conn.execute('PRAGMA user_version=1')
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match='state migrate'):
        SQLiteRepository(dbfile)
    from decisionbound.state_migration import migrate_database
    upgraded = tmp_path / 'upgraded.db'
    receipt = migrate_database(dbfile, upgraded)
    assert receipt.source_version == 1 and receipt.target_version == 2
    with sqlite3.connect(dbfile) as old:
        assert old.execute('PRAGMA user_version').fetchone()[0] == 1
    repo = SQLiteRepository(upgraded)
    with repo._connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute("SELECT name FROM sqlite_master WHERE name='repair_sessions'").fetchone()


def test_feedback_and_task_contract_are_source_free(project):
    result = run(project, ScriptedRepairFactory((PATCH,)), ScriptedTestRunner([1]),
                 limits=RepairLimits(max_attempts=1))
    raw = json.dumps(result.to_dict())
    assert 'VALUE = 2' not in raw and '.env' not in raw
    assert result.status == 'exhausted'
    assert len(result.attempts) == 1


def test_zero_approved_budget_cannot_start_real_model_factory(project):
    from decisionbound.code_repair import ModelRepairFactory
    from decisionbound.model_planner import Pricing
    class NoCallTransport:
        def complete(self, **kwargs):
            raise AssertionError('Should not send a paid request')
    factory = ModelRepairFactory(NoCallTransport(), Pricing(1,1))
    state = run(project, factory, ScriptedTestRunner([0]))
    assert state.status == 'exhausted'
    assert state.attempts == ()
