"""Sprint 07: supervised coding review and fail-closed handoff tests.

All positive receipts in this file are SYNTHETIC host-ledger fixtures. They
exercise the review algorithm, not Docker security or actual executed tests.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_review import (
    AllowReviewedEvidence, CodeReviewPolicy, CodeReviewService, ReviewBlocked,
)
from decisionbound.code_execution import PatchFileProposer
from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import (
    CodeEvidenceReviewWorker, CodingRepairWorker, IndependentTaskVerifier,
    TaskAssignment, WorkerReceipt, WorkflowConflict, WorkflowJournal,
    WorkflowSpec, WorkflowSupervisor, WorkflowTask, WorkflowWorker,
)


def _git(folder: Path, *args: str):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, timeout=15)


@pytest.fixture
def fixture(tmp_path):
    state = tmp_path / 'private'
    db = SQLiteRepository(state / 'state.sqlite3')
    project = tmp_path / 'repo'
    project.mkdir()
    (project / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
    _git(project, 'init', '-q')
    _git(project, 'config', 'user.name', 'Tests')
    _git(project, 'config', 'user.email', 'tests@example.invalid')
    _git(project, 'add', 'app.py')
    _git(project, 'commit', '-qm', 'initial')
    init_project(project, db)
    contract = TaskContract('change_value', ('app.py',), ('app.py',),
                            (('python', '-V'),),
                            (TextPredicate('app.py', contains='VALUE = 2'),))
    patch = ('diff --git a/app.py b/app.py\n'
             '--- a/app.py\n+++ b/app.py\n'
             '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n')
    spec = WorkflowSpec((
        WorkflowTask('plan', 'prepare', 'Prepare the change', role='planner'),
        WorkflowTask('implement', 'code', 'Apply approved patch', ('plan',), role='implementer'),
        WorkflowTask('audit', 'review', 'Review host evidence', ('implement',), role='reviewer'),
    ))
    journal = WorkflowJournal(db)
    wf = journal.create(project_path=project, spec=spec, idempotency_key='review_example_07')
    approved = {'implement': {'approved_writes': True, 'contract': contract,
                  'factory': ScriptedRepairFactory((patch,)), 'runner': ScriptedTestRunner([0]),
                  'limits': RepairLimits(max_attempts=1, max_model_cost_usd=0)}}
    return db, project, state, journal, wf, approved


def test_review_stage_requires_exact_code_dependency_and_role():
    with pytest.raises(ValueError, match='Review stages'):
        WorkflowSpec((WorkflowTask('a', 'prepare', 'p'),
                      WorkflowTask('b', 'review', 'review', ('a',), role='reviewer')))
    with pytest.raises(ValueError, match='Review stages'):
        WorkflowSpec((WorkflowTask('a', 'code', 'c'),
                      WorkflowTask('b', 'review', 'review', ('a',), role='general')))
    with pytest.raises(ValueError, match='Review stages'):
        WorkflowSpec((WorkflowTask('a', 'code', 'c'),
                      WorkflowTask('b', 'review', 'review', ('a',), role='reviewer', max_cost_usd=0.1)))
    with pytest.raises(ValueError, match='Review stages'):
        WorkflowSpec((WorkflowTask('a', 'review', 'x', role='reviewer'),))
    with pytest.raises(ValueError, match='Review stages'):
        WorkflowSpec((WorkflowTask('a', 'code', 'c'), WorkflowTask('c', 'code', 'c'),
                      WorkflowTask('b', 'review', 'review', ('a', 'c'), role='reviewer')))


def test_mock_code_stage_cannot_unlock_review(fixture):
    db, project, state, journal, wf, approved = fixture
    workers = {'prepare': __import__('decisionbound.workflows', fromlist=['PreparationWorker']).PreparationWorker(db),
               'code': CodingRepairWorker(repository=db, state_root=state, approved=approved),
               'review': CodeEvidenceReviewWorker(repository=db, state_root=state)}
    result = WorkflowSupervisor(journal, workers=workers,
                                verifier=IndependentTaskVerifier(db, state)).run(wf)
    assert result['state'] == 'needs_real_tests' or result['state'] == 'blocked'
    states = {item['task_id']: item['state'] for item in result['tasks']}
    assert states == {'plan': 'verified', 'implement': 'needs_real_tests', 'audit': 'blocked'}
    assert result['task_completed'] is False
    assert (project / 'app.py').read_text() == 'VALUE = 1\n'
    assert not [e for e in journal.events(wf) if e['task_id'] == 'audit' and e['event'] == 'claimed']


def test_untrusted_review_worker_cannot_forge_verified_stage(fixture):
    db, project, state, journal, wf, approved = fixture
    # Complete plan, then create a synthetic forged upstream task state.
    WorkflowSupervisor(journal).run(wf)
    with db._connection() as conn:
        conn.execute("UPDATE workflow_tasks SET state='verified', receipt_sha256=? "
                     "WHERE workflow_id=? AND task_id='implement'", ('f' * 64, wf))
    class Forge(WorkflowWorker):
        def execute(self, assignment):
            return WorkerReceipt('reviewed', evidence_sha256='f' * 64)
    result = WorkflowSupervisor(journal, workers={'review': Forge()},
                                verifier=IndependentTaskVerifier(db, state)).run(wf)
    assert {r['task_id']: r['state'] for r in result['tasks']}['audit'] == 'blocked'


def test_review_handoff_receipt_is_private_and_bounded(fixture):
    db, project, state, journal, wf, approved = fixture
    WorkflowSupervisor(journal).run(wf)
    with db._connection() as conn:
        conn.execute("UPDATE workflow_tasks SET state='verified',receipt_sha256=? "
                     "WHERE workflow_id=? AND task_id='implement'", ('a' * 64, wf))
    claimed = journal.claim(wf, worker_kinds={'review'})
    assert claimed.task.role == 'reviewer'
    assert claimed.handoffs[0].kind == 'code'
    assert claimed.handoffs[0].receipt_sha256 == 'a' * 64
    assignment = claimed.assignment()
    assert not hasattr(assignment, 'owner_token')
    assert not hasattr(assignment, 'generation')
    with pytest.raises(ReviewBlocked):
        CodeEvidenceReviewWorker(repository=db, state_root=state).execute(assignment)


def test_review_custom_policy_can_only_reject(fixture):
    db, project, state, journal, wf, approved = fixture
    class No( CodeReviewPolicy):
        def accept(self, evidence):
            return False
    assert isinstance(No(), CodeReviewPolicy)
    with pytest.raises(TypeError):
        CodeEvidenceReviewWorker(repository=db, state_root=state, policy=lambda _: True)
    assert isinstance(AllowReviewedEvidence(), CodeReviewPolicy)


def test_positive_synthetic_host_receipt_then_artifact_corruption(fixture):
    """Positive fixture tests cross-ledger consistency, *not* actual Docker."""
    db, project, state, journal, wf, approved = fixture
    WorkflowSupervisor(journal).run(wf)  # verify planner
    from decisionbound.workflows import _code_idempotency
    from decisionbound.code_journal import SQLiteRepairJournal
    from decisionbound.code_workspace import GitWorktreeManager
    from decisionbound.identity import load_project

    code_receipt = '1' * 64
    _, head = GitWorktreeManager.check_source(project)
    info = {'project_id': load_project(project, db).project_id,
            'contract_sha256': approved['implement']['contract'].sha256}
    sess, _ = SQLiteRepairJournal(db, state).open(idempotency_key=_code_idempotency(wf, 'implement'),
            request_sha256='a' * 64, project_id=info['project_id'],
            contract_sha256=info['contract_sha256'], max_attempts=1)
    attempt = SQLiteRepairJournal(db, state).reserve(sess.session_id)
    data = approved['implement']['factory'].patches[0].encode('utf-8')
    sha = hashlib.sha256(data).hexdigest()
    patch_dir = state / 'code-artifacts'
    patch_dir.mkdir(mode=0o700)
    artifact = patch_dir / f'{attempt.run_id}.patch'
    artifact.write_bytes(data)
    test_receipts = [{'kind': 'docker', 'exit_code': 0, 'timed_out': False,
                      'receipt_sha256': hashlib.sha256(b'synthetic').hexdigest()}]
    synthetic = [
        ('code_started', {'project_id': info['project_id'], 'write_approved': True,
                          'contract_sha256': info['contract_sha256'], 'test_count': 1}),
        ('code_worktree_created', {'base_commit': head}),
        ('code_patch_applied', {'sha256': sha, 'size_bytes': len(data), 'paths': ['app.py'],
                                'diff_sha256': 'd' * 64}),
        ('code_test_receipt', test_receipts[0]),
        ('code_post_test_integrity', {'unchanged': True, 'diff_sha256': 'd' * 64}),
        ('code_contract_check', {'passed': True, 'contract_sha256': info['contract_sha256']}),
        ('code_verification', {'contract_verified': True}),
        ('code_workspace_cleaned', {'ok': True}),
        ('code_finished', {'contract_sha256': info['contract_sha256'],
                            'contract_verified': True, 'acceptance_verified': True}),
    ]
    for kind, payload in synthetic:
        db.record_event(attempt.run_id, kind, payload)
    SQLiteRepairJournal(db, state).finish_attempt(sess.session_id, attempt.run_id, status='passed',
        summary={'patch_sha256': sha, 'contract_verified': True}, cost_delta=0)
    SQLiteRepairJournal(db, state).close(sess.session_id, 'verified')
    with db._connection() as conn:
        conn.execute("UPDATE workflow_tasks SET state='verified',receipt_sha256=? "
                     "WHERE workflow_id=? AND task_id='implement'", (code_receipt, wf))
    # Valid host-ledger fixture yields deterministic evidence.
    reviewer = CodeReviewService(db, state)
    evidence = reviewer.examine(workflow_id=wf, project_id=info['project_id'],
                               project_path=project, code_task_id='implement',
                               expected_receipt_sha256=code_receipt)
    lease = journal.claim(wf, worker_kinds={'review'})
    worker = CodeEvidenceReviewWorker(repository=db, state_root=state)
    receipt = worker.execute(lease.assignment())
    assert receipt.evidence_sha256 == evidence.sha256
    class Veto(CodeReviewPolicy):
        def accept(self, evidence):
            return False
    with pytest.raises(ReviewBlocked, match='policy'):
        CodeEvidenceReviewWorker(repository=db, state_root=state,
                                 policy=Veto()).execute(lease.assignment())
    assert IndependentTaskVerifier(db, state).verify(
        lease, WorkerReceipt('reviewed', evidence_sha256=evidence.sha256,
                             cost_usd=0.01)) == 'blocked'
    assert IndependentTaskVerifier(db, state).verify(lease, receipt) == 'verified'
    journal.finish(lease, state='verified', receipt=receipt,
                   verifier=IndependentTaskVerifier(db, state))
    assert journal.status(wf)['state'] == 'verified_stages'
    assert journal.attest(wf, state_root=state)['review_attestation'] == 'current'
    # A tampered artifact immediately invalidates the same receipt.
    artifact.write_bytes(data + b'\n# modified')
    assert journal.attest(wf, state_root=state)['review_attestation'] == 'stale'
    assert IndependentTaskVerifier(db, state).verify(lease, receipt) == 'blocked'
    with pytest.raises(ReviewBlocked, match='integrity'):
        reviewer.examine(workflow_id=wf, project_id=info['project_id'],
                         project_path=project, code_task_id='implement',
                         expected_receipt_sha256=code_receipt)
    # Restoring the artifact is insufficient if Git HEAD has subsequently moved.
    artifact.write_bytes(data)
    assert journal.attest(wf, state_root=state)['review_attestation'] == 'current'
    (project / 'README.md').write_text('new checked-in readme\n')
    _git(project, 'add', 'README.md')
    _git(project, 'commit', '-qm', 'new base')
    assert journal.attest(wf, state_root=state)['review_attestation'] == 'stale'
    assert journal.status(wf)['state'] == 'verified_stages'  # historical audit unchanged
