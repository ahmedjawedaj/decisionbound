"""Offline contract tests for mandatory test-first receipts, not real Docker."""
from __future__ import annotations

import hashlib
import json

from decisionbound.code_execution import CodeReceiptVerifier, _test_receipt_hash
from decisionbound.code_models import PatchInspection, TestOutcome
from decisionbound.code_repair import RepairLimits, _request_digest
from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.storage import SQLiteRepository


def test_red_green_host_receipt_binds_same_exact_regression_command(tmp_path):
    repo = SQLiteRepository(tmp_path/'state.sqlite3')
    run_id = 'code_' + 'b'*32
    patch = PatchInspection(('app.py',), 1, 1, 'a'*64, 123)
    diff_sha = 'd'*64
    command = ('python', '-c', 'assert True')
    post = TestOutcome('docker', command, 0, False, 100, 'a'*64, 'b'*64)
    hash_command = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    repo.record_event(run_id, 'code_started', {'requires_red_test': True})
    repo.record_event(run_id, 'code_patch_applied', {
        'sha256':patch.sha256, 'diff_sha256':diff_sha, 'paths':list(patch.paths)})
    repo.record_event(run_id, 'code_post_test_integrity', {
        'unchanged':True, 'diff_sha256':diff_sha})
    repo.record_event(run_id, 'code_test_receipt', {
        'receipt_sha256':_test_receipt_hash(post), 'kind':'docker', 'exit_code':0,
        'timed_out':False, 'command_sha256':hash_command})
    checker = CodeReceiptVerifier(repo)
    valid, reason = checker.verify(run_id, patch, diff_sha, (post,))
    assert not valid and 'baseline' in reason
    # An unrelated, failed command must not count as red for another regression.
    repo.record_event(run_id, 'code_prepatch_test_receipt', {
        'kind':'docker', 'exit_code':1, 'timed_out':False, 'output_truncated':False,
        'receipt_sha256':'c'*64, 'command_sha256':'0'*64})
    valid, reason = checker.verify(run_id, patch, diff_sha, (post,))
    assert not valid and 'baseline' in reason
    # Matching synthetic host metadata tests the verifier's algorithm only.
    with repo._connection() as db:
        db.execute('UPDATE run_events SET payload=? WHERE run_id=? AND event=?',
                   (json.dumps({'kind':'docker','exit_code':1,'timed_out':False,
                    'output_truncated':False,'receipt_sha256':'c'*64,
                    'command_sha256':hash_command}),run_id,'code_prepatch_test_receipt'))
    valid, reason = checker.verify(run_id, patch, diff_sha, (post,))
    assert valid and 'receipted' in reason


def test_default_repair_digest_remains_backward_compatible(tmp_path):
    contract = TaskContract('bugfix', ('app.py',), ('app.py',),
        (('python','-V'),), (TextPredicate('app.py', contains='value'),))
    limits = RepairLimits(max_attempts=1)
    data = {'project_id':'project', 'base_commit':'f'*40,
            'task_sha256':hashlib.sha256(b'fix').hexdigest(),
            'contract_sha256':contract.sha256, 'sources':['app.py'],
            'max_attempts':1, 'max_cost_usd':0.0, 'timeout_seconds':60}
    legacy = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    now = _request_digest(project_id='project', base_commit='f'*40,
        task='fix', contract=contract, sources=('app.py',), limits=limits,
        timeout_seconds=60)
    assert now == legacy
    with_red = _request_digest(project_id='project', base_commit='f'*40,
        task='fix', contract=contract, sources=('app.py',), limits=limits,
        timeout_seconds=60, require_red_test=True)
    assert with_red != legacy
