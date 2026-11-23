"""Sprint 07 supervised coding SDK/CLI (fully offline, noncertifying fixtures)."""
from __future__ import annotations

import json
import re
import subprocess

import pytest
from typer.testing import CliRunner

from decisionbound.cli import app
from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.supervised_coding import SupervisedCodingOrchestrator, SupervisedCodingRequest
from decisionbound.workflows import WorkflowJournal


PATCH = ('diff --git a/app.py b/app.py\n'
         '--- a/app.py\n+++ b/app.py\n'
         '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / 'private'
    monkeypatch.setenv('DBOUND_HOME', str(home))
    store = SQLiteRepository(home / 'state.sqlite3')
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'app.py').write_text('VALUE = 1\n')
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Tester'], check=True)
    subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(root), 'add', 'app.py'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'start'], check=True)
    init_project(root, store)
    contract = TaskContract('test_change', ('app.py',), ('app.py',),
                            (('python', '-c', 'import app; assert app.VALUE == 2'),),
                            (TextPredicate('app.py', contains='VALUE = 2'),))
    return store, home, root, contract


def orchestrator(repo, patches=(PATCH,), exit_codes=(0,)):
    store, home, root, contract = repo
    return SupervisedCodingOrchestrator(repository=store, state_root=home,
                                       proposer_factory=ScriptedRepairFactory(patches),
                                       runner=ScriptedTestRunner(list(exit_codes)))


def request(repo, attempts=1):
    return SupervisedCodingRequest('Change value to two', repo[3], ('app.py',),
                                   limits=RepairLimits(max_attempts=attempts))


def test_full_mock_supervised_flow_fails_closed_and_does_not_edit_checkout(repo):
    store, home, root, contract = repo
    workflow = orchestrator(repo).execute(project_path=root, request=request(repo),
                                         idempotency_key='code_review_demo', approved_writes=True)
    states = {s['task_id']: s['state'] for s in workflow['tasks']}
    assert states == {'plan': 'verified', 'implement': 'needs_real_tests', 'audit': 'blocked'}
    assert workflow['task_completed'] is False
    assert workflow['patch_auto_merged'] is False
    assert (root / 'app.py').read_text() == 'VALUE = 1\n'
    events = WorkflowJournal(store).events(workflow['workflow_id'])
    assert any(e['event'] == 'dependency_blocked' and e['task_id'] == 'audit' for e in events)
    assert not any(e['event'] == 'claimed' and e['task_id'] == 'audit' for e in events)


def test_repeated_idempotency_key_does_not_reexecute_patch(repo):
    runner = orchestrator(repo)
    spec = request(repo)
    first = runner.execute(project_path=repo[2], request=spec,
                           idempotency_key='repeat_code_run', approved_writes=True)
    second = runner.execute(project_path=repo[2], request=spec,
                            idempotency_key='repeat_code_run', approved_writes=True)
    assert second['workflow_id'] == first['workflow_id']
    events = WorkflowJournal(repo[0]).events(first['workflow_id'])
    assert len([e for e in events if e['task_id']=='implement' and e['event']=='claimed']) == 1


def test_requires_explicit_writes_consent_before_creating_workflow(repo):
    runner = orchestrator(repo)
    with pytest.raises(PermissionError, match='explicit'):
        runner.execute(project_path=repo[2], request=request(repo),
                       idempotency_key='no_approval')
    with repo[0]._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='workflows'").fetchone()[0] == 0


def test_invalid_scope_contract_is_rejected_before_workflow(repo):
    bad = TaskContract('danger', ('.env',), ('.env',), (('python','-V'),),
                       (TextPredicate('.env', contains='X'),))
    with pytest.raises(Exception, match='Protected'):
        orchestrator(repo).execute(project_path=repo[2],
            request=SupervisedCodingRequest('Bad', bad),
            idempotency_key='bad_approve', approved_writes=True)


def test_request_rejects_unknown_sources_or_oversize_budget(repo):
    with pytest.raises(ValueError, match='bounds'):
        SupervisedCodingRequest('Good', repo[3], ('other.py',))
    with pytest.raises(ValueError):
        SupervisedCodingRequest('', repo[3])
    with pytest.raises(TypeError):
        SupervisedCodingRequest('Good', None)


def test_failed_test_repair_then_mock_passes_but_not_verified(repo):
    runner = orchestrator(repo, patches=(PATCH, PATCH), exit_codes=(1, 0))
    result = runner.execute(project_path=repo[2], request=request(repo, attempts=2),
                            idempotency_key='repair_workflow', approved_writes=True)
    assert {t['task_id']:t['state'] for t in result['tasks']} == {
        'plan':'verified','implement':'needs_real_tests','audit':'blocked'}
    with repo[0]._connection() as db:
        assert db.execute('SELECT COUNT(*) FROM repair_attempts').fetchone()[0] == 2


def test_cli_supervised_code_requires_approval_and_emits_shared_workflow(repo, tmp_path):
    store, home, root, contract = repo
    approved = tmp_path / 'contract.json'
    approved.write_text(json.dumps(contract.to_dict()))
    patch = tmp_path / 'change.patch'
    patch.write_text(PATCH)
    command = ['workflow','supervised-code','Change value to two',
               '--contract',str(approved),'--patch-file',str(patch),
               '--path',str(root),'--key','command_review_demo','--source-target','app.py']
    client = CliRunner()
    assert client.invoke(app, command).exit_code != 0
    response = client.invoke(app, command+['--approve-writes'])
    assert response.exit_code == 0, response.output
    state = json.loads(response.output)
    assert state['state'] == 'blocked'  # reviewer correctly refuses mock code
    assert state['tasks'][2]['state'] == 'blocked'
    assert json.loads(client.invoke(app, ['workflow','status',state['workflow_id']]).output)['tasks'] == state['tasks']
    attestation = json.loads(client.invoke(app, ['workflow','attest',state['workflow_id']]).output)
    assert attestation['review_attestation'] == 'not_verified'
    assert (root / 'app.py').read_text() == 'VALUE = 1\n'
    from fastapi.testclient import TestClient
    from decisionbound.web import create_app, local_token
    web = TestClient(create_app())
    url = '/api/workflows/'+state['workflow_id']+'/attestation'
    assert web.get(url).status_code == 401
    from_web = web.get(url, headers={'Authorization':'Bearer '+local_token()})
    assert from_web.status_code == 200
    assert from_web.json() == attestation
    for incompatible in (['--mode','docker'], ['--allow-docker'], ['--docker-image','python:3.12-slim']):
        assert client.invoke(app, command+['--approve-writes']+incompatible).exit_code != 0


def test_regression_red_gate_rejects_simulated_failing_test(repo):
    # Even a fake nonzero baseline exit code cannot certify a regression.
    store, home, root, contract = repo
    runner = orchestrator(repo, patches=(PATCH,), exit_codes=(1, 0))
    requested = SupervisedCodingRequest('Fix regression', contract, ('app.py',),
        limits=RepairLimits(max_attempts=1), require_red_test=True)
    result = runner.execute(project_path=root, request=requested,
                            idempotency_key='mock_red_rejected', approved_writes=True)
    assert result['state'] == 'blocked'
    assert {t['task_id']:t['state'] for t in result['tasks']} == {
        'plan':'verified','implement':'blocked','audit':'blocked'}
    assert (root/'app.py').read_text() == 'VALUE = 1\n'
    with store._connection() as db:
        attempt = db.execute('SELECT run_id FROM repair_attempts').fetchone()
    events = store.events(attempt['run_id'])
    assert any(e['event'] == 'code_prepatch_test_receipt' and
               e['payload']['kind']=='mock' for e in events)
    assert not any(e['event'] == 'code_patch_applied' for e in events)


def test_idempotency_key_binds_contract_runner_and_patch_hash(repo):
    from decisionbound.workflows import WorkflowConflict
    root = repo[2]
    start = orchestrator(repo).execute(project_path=root, request=request(repo),
            idempotency_key='immutable_approval', approved_writes=True)
    altered_patch = PATCH.replace('+VALUE = 2', '+VALUE = 3')
    with pytest.raises(WorkflowConflict, match='different request'):
        orchestrator(repo, patches=(altered_patch,)).execute(
            project_path=root, request=request(repo),
            idempotency_key='immutable_approval', approved_writes=True)
    altered_request = SupervisedCodingRequest('Change value to two', repo[3], ('app.py',),
        require_red_test=True)
    with pytest.raises(WorkflowConflict, match='different request'):
        orchestrator(repo).execute(project_path=root, request=altered_request,
            idempotency_key='immutable_approval', approved_writes=True)
    assert start['state'] == 'blocked'
    with repo[0]._connection() as db:
        assert db.execute('SELECT COUNT(*) FROM workflows').fetchone()[0] == 1


def test_workflow_spec_canonical_approval_hash_is_versioned(repo):
    request_ = request(repo)
    from decisionbound.workflows import WorkflowSpec
    fingerprint = 'f'*64
    encoded = request_.workflow(approval_sha256=fingerprint).to_dict()
    assert encoded['approval_sha256'] == fingerprint
    assert WorkflowSpec.from_dict(encoded).to_dict() == encoded
    with pytest.raises(ValueError, match='fingerprint'):
        request_.workflow(approval_sha256='bad')


def test_cli_supervised_code_docker_mode_refuses_a_tag_in_strict_digest_mode_cleanly(repo, tmp_path, monkeypatch):
    store, home, root, contract = repo
    monkeypatch.setenv('DBOUND_REQUIRE_IMAGE_DIGEST', '1')
    approved = tmp_path / 'contract.json'
    approved.write_text(json.dumps(contract.to_dict()))
    patch = tmp_path / 'change.patch'
    patch.write_text(PATCH)
    result = CliRunner().invoke(app, ['workflow', 'supervised-code', 'Change value to two',
                                      '--contract', str(approved), '--patch-file', str(patch),
                                      '--path', str(root), '--key', 'strict_digest_demo', '--source-target', 'app.py',
                                      '--mode', 'docker', '--docker-image', 'python:3.12',
                                      '--allow-docker', '--approve-writes'])
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit)
    assert 'sha256 digest' in ' '.join(re.sub('[│╭╮╰╯─]', ' ', result.output).split())
