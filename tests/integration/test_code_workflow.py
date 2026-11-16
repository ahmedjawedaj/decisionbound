"""End-to-end Git write isolation, mocked test dispatch and failure recovery."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path
import pytest
from decisionbound.code_execution import CodeReceiptVerifier, PatchFileProposer, run_coding_task
from decisionbound.code_models import TestOutcome
from decisionbound.code_ports import CodingVerifier
from decisionbound.code_patches import PatchPolicy, PatchValidator, UnsafePatch
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.code_workspace import GitWorktreeManager, WorkspaceError
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository

PATCH = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
'''
ADD = '''diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+NEW = True
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], text=True,
                          capture_output=True, check=True).stdout.strip()


@pytest.fixture
def project(tmp_path: Path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test Agent')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
    git(root, 'add', 'app.py')
    git(root, 'commit', '-qm', 'initial')
    state = tmp_path / 'state'
    db = SQLiteRepository(state / 'state.sqlite3')
    init_project(root, db, 'fixture', ('python',))
    return root, state, db


def execute(project, patch=PATCH, checker=None, **kwargs):
    root, state, db = project
    return run_coding_task(project_path=root, state_root=state, repository=db, task='Update the value',
                           proposer=PatchFileProposer(patch),
                           runner=kwargs.pop('runner', ScriptedTestRunner(checker=checker)),
                           test_commands=kwargs.pop('test_commands', (('python', '-m', 'pytest', '-q'),)),
                           approved_writes=kwargs.pop('approved_writes', True), **kwargs)


def test_end_to_end_patch_and_mock_tests_do_not_claim_completion(project):
    root, state, db = project
    outcome = execute(project, checker=lambda ws, cmd: 0 if (Path(ws.path)/'app.py').read_text() == 'VALUE = 2\n' else 1)
    assert outcome.changed_paths == ('app.py',)
    assert outcome.acceptance_verified is False
    assert outcome.task_completed is False
    assert outcome.tests[0].kind == 'mock'
    assert Path(outcome.patch_artifact).read_text() == PATCH
    assert (root/'app.py').read_text() == 'VALUE = 1\n'
    assert not (state/'code-worktrees'/outcome.run_id).exists()
    events = db.events(outcome.run_id)
    assert [e['event'] for e in events][-2:] == ['code_workspace_cleaned', 'code_finished']
    assert 'VALUE = 2' not in json.dumps(events)
    assert events[-1]['payload']['task_completed'] is False
    assert GitWorktreeManager(state).pending() == []


def test_new_file_and_scope_verification(project):
    result = execute(project, patch=ADD, checker=lambda ws, c: 0 if (Path(ws.path)/'new.py').exists() else 2)
    assert result.changed_paths == ('new.py',)
    assert result.tests[0].exit_code == 0
    assert not (project[0]/'new.py').exists()


def test_no_approval_means_no_run_or_worktree(project):
    with pytest.raises(PermissionError):
        execute(project, approved_writes=False)
    assert not (project[1]/'code-worktrees').exists()


def test_rejects_dirty_source(project):
    (project[0]/'app.py').write_text('DIRTY\n')
    with pytest.raises(WorkspaceError, match='unrelated'):
        execute(project)
    assert GitWorktreeManager(project[1]).pending() == []


def test_failing_mock_test_still_preserves_patch_but_not_original(project):
    result = execute(project, runner=ScriptedTestRunner([1]))
    assert not result.acceptance_verified
    assert result.tests[0].exit_code == 1
    assert (project[0]/'app.py').read_text() == 'VALUE = 1\n'
    assert GitWorktreeManager(project[1]).pending() == []


def test_test_modifies_approved_file_is_detected(project):
    def mutate(ws, command):
        (Path(ws.path)/'app.py').write_text('VALUE = 999\n')
        return 0
    result = execute(project, checker=mutate)
    assert not result.acceptance_verified
    integrity = [e['payload'] for e in project[2].events(result.run_id) if e['event'] == 'code_post_test_integrity']
    assert integrity[0]['unchanged'] is False


def test_extra_file_written_by_test_is_detected(project):
    def mutate(ws, command):
        (Path(ws.path)/'sneaky.py').write_text('hello\n')
        return 0
    result = execute(project, checker=mutate)
    integrity = [e['payload'] for e in project[2].events(result.run_id) if e['event'] == 'code_post_test_integrity']
    assert integrity[0]['unchanged'] is False


def test_ignored_file_written_by_test_is_detected(project):
    # A code runner that drops a .pyc must not leave unnoticed modifications.
    def mutate(ws, command):
        (Path(ws.path)/'.pytest_cache').mkdir()
        (Path(ws.path)/'.pytest_cache'/'evil').write_text('cache')
        return 0
    result = execute(project, checker=mutate)
    assert not result.acceptance_verified
    integrity = [e['payload'] for e in project[2].events(result.run_id) if e['event'] == 'code_post_test_integrity']
    assert not integrity[0]['unchanged']


def test_unexpected_tool_run_failure_is_recorded_and_cleaned(project):
    class BrokenRunner(ScriptedTestRunner):
        def run(self, *args):
            raise RuntimeError('simulated failure')
    with pytest.raises(RuntimeError):
        execute(project, runner=BrokenRunner())
    records = list((project[1]/'code-records').glob('code_*.json'))
    assert len(records) == 1
    run_id = records[0].stem
    assert GitWorktreeManager(project[1]).pending() == []
    assert any(e['event'] == 'code_failed' for e in project[2].events(run_id))


def test_recovery_of_interrupted_workspace(project):
    root, state, db = project
    manager = GitWorktreeManager(state)
    rid = 'code_' + uuid.uuid4().hex
    worktree = manager.create(root, rid)
    assert manager.pending() == [rid]
    assert manager.reconcile(rid) == worktree
    assert manager.cleanup(rid)
    assert manager.cleanup(rid) is False
    assert manager.pending() == []
    assert root.joinpath('app.py').read_text() == 'VALUE = 1\n'


def test_run_id_reconciliation_rejects_copied_record(project):
    root, state, _ = project
    manager = GitWorktreeManager(state)
    rid = 'code_' + uuid.uuid4().hex
    manager.create(root, rid)
    data = state/'code-records'/f'{rid}.json'
    obj = json.loads(data.read_text())
    obj['worktree'] = str(root)
    data.write_text(json.dumps(obj))
    with pytest.raises(WorkspaceError):
        manager.cleanup(rid)
    # restore the record to clean this fixture
    obj['worktree'] = str(state/'code-worktrees'/rid)
    data.write_text(json.dumps(obj))
    assert manager.cleanup(rid)


def test_custom_verifier_cannot_grant_mock_acceptance(project):
    class CompromisedVerifier(CodingVerifier):
        def verify(self, *args):
            return True, 'trust me'
    result = execute(project, verifier=CompromisedVerifier())
    assert not result.acceptance_verified
    assert 'Only the built-in' in result.verification_reason or 'Mock' in result.verification_reason


def test_verifier_rejects_manually_forged_receipt(project):
    root, state, db = project
    outcome = execute(project)
    ev = [e for e in db.events(outcome.run_id) if e['event'] == 'code_patch_applied'][0]
    from decisionbound.code_models import PatchInspection
    ins = PatchInspection(('app.py',), 1, 1, outcome.patch_sha256, len(PATCH.encode()))
    forged = TestOutcome('docker', ('python',), 0, False, 1, 'a'*64, 'b'*64)
    ok, _ = CodeReceiptVerifier(db).verify(outcome.run_id, ins, outcome.diff_sha256, (forged,))
    assert not ok


def test_patch_file_artifact_has_owner_only_permissions(project):
    result = execute(project)
    assert Path(result.patch_artifact).is_file()
    if os.name == 'posix':  # NTFS has no POSIX mode bits to assert on.
        mode = Path(result.patch_artifact).stat().st_mode & 0o777
        assert mode == 0o600


def test_two_mock_test_commands_and_early_failure(project):
    # Tests are sequential and subsequent commands are skipped after failure.
    runner = ScriptedTestRunner([0, 1])
    result = execute(project, runner=runner,
                     test_commands=(('python', '-m', 'compileall', '-q', '.'), ('python', '-m', 'pytest', '-q'),
                                    ('python', '-m', 'unittest', '-q')))
    assert len(result.tests) == 2 and len(runner.calls) == 2
    assert not result.acceptance_verified


def test_model_selected_source_file_must_be_explicit_and_nonsecret(project):
    with pytest.raises(ValueError, match='does not exist'):
        execute(project, source_targets=('unknown.py',))
    with pytest.raises(UnsafePatch):
        execute(project, source_targets=('.decisionbound/project.yaml',))


def test_selected_file_is_transmitted_only_to_explicit_proposer(project):
    from decisionbound.code_ports import PatchProposer
    from decisionbound.code_models import PatchProposal
    class InspectingProposer(PatchProposer):
        def propose(self, task, source_files):
            assert source_files == {'app.py': 'VALUE = 1\n'}
            return PatchProposal(PATCH)
    result = run_coding_task(project_path=project[0], state_root=project[1], repository=project[2],
                             task='change', proposer=InspectingProposer(), runner=ScriptedTestRunner([0]),
                             test_commands=(('python','-V'),), source_targets=('app.py',), approved_writes=True)
    assert result.changed_paths == ('app.py',)


def test_docker_simulated_receipt_positive_branch(project, monkeypatch):
    from decisionbound.code_sandbox import DockerTestRunner
    import subprocess
    actual_run = subprocess.run
    def fake_docker(argv, **kwargs):
        if argv[0] == 'docker':
            return subprocess.CompletedProcess(argv, 0, b'All assertions passed', b'')
        return actual_run(argv, **kwargs)
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run', fake_docker)
    # This only tests the host receipt control flow; no container ran.
    result = execute(project, runner=DockerTestRunner('python:3.12'),
                     test_commands=(('python', '-c', 'assert 1 + 1 == 2'),))
    assert result.acceptance_verified
    assert not result.task_completed


def test_clean_crlf_checkout_with_user_autocrlf_is_accepted(tmp_path, monkeypatch):
    # Git for Windows enables core.autocrlf in its system config, which the
    # isolated Git environment hides. A clean CRLF checkout must not look dirty.
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.gitconfig').write_text('[core]\n\tautocrlf = true\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('GIT_CONFIG_GLOBAL', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    root = tmp_path / 'crlf-repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test Agent')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'app.py').write_bytes(b'VALUE = 1\r\n')
    git(root, 'add', 'app.py')
    git(root, 'commit', '-qm', 'initial')
    manager = GitWorktreeManager(tmp_path / 'state')
    assert manager.check_source(root)[1] == git(root, 'rev-parse', 'HEAD')
    (root / 'app.py').write_bytes(b'VALUE = 2\r\n')
    with pytest.raises(WorkspaceError, match='unrelated changes'):
        manager.check_source(root)
