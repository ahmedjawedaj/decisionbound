"""The host must not let Git read a `.git` entry that sandboxed test code could have replaced.

The Docker runner mounts the disposable worktree read/write, so a test command can delete the `.git`
file inside it and create a `.git` directory with its own config. Git would then read that config and
may run a program it names (here `core.fsmonitor`) as the host user. These tests plant such a directory
and check that no host-side Git call used by the coding workflow runs it.

POSIX only: the planted program is a shell script.
"""
from __future__ import annotations
import os
import stat
import subprocess
import sys
from pathlib import Path
import pytest
from decisionbound.code_execution import PatchFileProposer, _changed_diff, run_coding_task
from decisionbound.code_patches import PatchValidator
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.code_models import Workspace
from decisionbound.code_workspace import (GitWorktreeManager, WorkspaceError, _git, _line_ending_options,
                                          git_entry_is_original, worktree_git_dir)
from tests.integration.test_code_workflow import ADD, PATCH, project  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='the planted program is a POSIX shell script')


def make_workspace(project):
    root, state, _ = project
    return GitWorktreeManager(state).create(root, 'code_' + '1' * 32)


def plant(workspace, marker: Path, extra_config: str = '') -> None:
    """Do what a test command could do: replace the worktree's `.git` file with a hostile directory."""
    tree = Path(workspace.path)
    (tree / '.git').unlink()
    hostile = tree / '.git'
    (hostile / 'objects').mkdir(parents=True)
    (hostile / 'refs').mkdir()
    (hostile / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8')
    program = hostile / 'monitor.sh'
    program.write_text(f'#!/bin/sh\ntouch "{marker}"\nprintf "\\0"\n', encoding='utf-8')
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    (hostile / 'config').write_text(
        f'[core]\n\trepositoryformatversion = 0\n\tfsmonitor = {program}\n{extra_config}', encoding='utf-8')


def test_control_plain_git_in_a_tampered_worktree_does_run_the_planted_program(project, tmp_path):
    """Proves the planted directory works on this Git, so the passing tests below are not vacuous."""
    ws, marker = make_workspace(project), tmp_path / 'ran'
    plant(ws, marker)
    try:
        _git(Path(ws.path), 'status', '--porcelain')
    except WorkspaceError:
        pass
    assert marker.exists()


def test_metadata_directory_comes_from_the_source_repository_not_the_worktree(project, tmp_path):
    ws = make_workspace(project)
    expected = worktree_git_dir(ws)
    assert Path(ws.repository_root).resolve() in expected.parents
    assert Path(ws.path).resolve() not in expected.parents
    plant(ws, tmp_path / 'ran')
    assert worktree_git_dir(ws) == expected
    (Path(ws.path) / '.git').rename(Path(ws.path) / 'moved')
    (Path(ws.path) / '.git').write_text(f'gitdir: {tmp_path}/elsewhere\n', encoding='utf-8')
    assert worktree_git_dir(ws) == expected


def test_a_worktree_the_repository_does_not_list_is_refused(project, tmp_path):
    ws = make_workspace(project)
    pointer = worktree_git_dir(ws) / 'gitdir'
    pointer.write_text(str(tmp_path / 'other' / '.git') + '\n', encoding='utf-8')
    with pytest.raises(WorkspaceError, match='exactly one'):
        worktree_git_dir(ws)


def test_two_entries_pointing_at_one_worktree_are_refused(project):
    ws = make_workspace(project)
    entry = worktree_git_dir(ws)
    twin = entry.parent / 'twin'
    twin.mkdir()
    (twin / 'gitdir').write_text((entry / 'gitdir').read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(WorkspaceError, match='exactly one'):
        worktree_git_dir(ws)


def test_the_post_test_diff_does_not_run_a_program_planted_in_the_worktree(project, tmp_path):
    ws, marker = make_workspace(project), tmp_path / 'ran'
    inspection = PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    before, _ = _changed_diff(ws, inspection)
    plant(ws, marker)
    after, diff = _changed_diff(ws, inspection)
    assert not marker.exists()
    assert after == before
    assert 'VALUE = 2' in diff


def test_patch_apply_and_the_mode_check_do_not_run_a_planted_program(project, tmp_path):
    ws, marker = make_workspace(project), tmp_path / 'ran'
    plant(ws, marker)
    PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    assert not marker.exists()
    assert 'VALUE = 2' in (Path(ws.path) / 'app.py').read_text(encoding='utf-8')


def test_line_ending_settings_are_not_read_from_a_planted_config(project, tmp_path):
    ws = make_workspace(project)
    plant(ws, tmp_path / 'ran', extra_config='\teol = crlf\n')
    assert 'core.eol=crlf' not in _line_ending_options(Path(ws.path), worktree_git_dir(ws))
    assert 'core.eol=crlf' in _line_ending_options(Path(ws.path))  # control: the planted file is read without the fix


def test_end_to_end_a_test_command_that_replaces_git_cannot_run_host_code(project, tmp_path):
    root, state, db = project
    marker = tmp_path / 'ran'

    def hostile_tests(workspace, command):
        if not (Path(workspace.path) / '.git').is_dir():
            plant(workspace, marker)
        return 0

    outcome = run_coding_task(project_path=root, state_root=state, repository=db, task='Update the value',
                              proposer=PatchFileProposer(PATCH), runner=ScriptedTestRunner(checker=hostile_tests),
                              test_commands=(('python', '-m', 'pytest', '-q'),), approved_writes=True)
    assert not marker.exists()
    events = {e['event']: e['payload'] for e in db.events(outcome.run_id)}
    assert events['code_post_test_integrity']['unchanged'] is False
    assert outcome.acceptance_verified is False
    assert outcome.task_completed is False


def test_new_file_patches_do_not_run_a_planted_program_when_the_host_stages_them(project, tmp_path):
    """`git add -N` only runs for new files, so it needs a patch that creates one."""
    ws, marker = make_workspace(project), tmp_path / 'ran'
    git_dir = worktree_git_dir(ws)
    inspection = PatchValidator().apply(ADD, Path(ws.path), git_dir=git_dir)
    plant(ws, marker)
    _, diff = _changed_diff(ws, inspection)
    assert not marker.exists()
    assert diff


def test_the_workflow_gives_the_patch_step_the_trusted_directory(project, monkeypatch):
    """The test command is not the only time the worktree is touched: a red test can run before the patch."""
    root, state, db = project
    seen = []
    real = PatchValidator.apply

    def spy(self, patch, workspace, git_dir=None):
        seen.append((Path(workspace), git_dir))
        return real(self, patch, workspace, git_dir=git_dir)

    monkeypatch.setattr(PatchValidator, 'apply', spy)
    run_coding_task(project_path=root, state_root=state, repository=db, task='Update the value',
                    proposer=PatchFileProposer(PATCH), runner=ScriptedTestRunner(),
                    test_commands=(('python', '-m', 'pytest', '-q'),), approved_writes=True)
    assert len(seen) == 1 and seen[0][1] is not None
    assert seen[0][1].parent.name == 'worktrees'


def test_git_is_not_given_line_ending_settings_from_a_planted_config(project, tmp_path, monkeypatch):
    ws = make_workspace(project)
    plant(ws, tmp_path / 'ran', extra_config='\teol = crlf\n')
    calls = []
    real = subprocess.run
    monkeypatch.setattr('decisionbound.code_workspace.subprocess.run',
                        lambda argv, *a, **kw: calls.append(list(argv)) or real(argv, *a, **kw))
    _git(Path(ws.path), 'status', '--porcelain', git_dir=worktree_git_dir(ws))
    assert calls and not any('core.eol=crlf' in argv for argv in calls)
    assert not (tmp_path / 'ran').exists()


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    return subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)


def program(path: Path, marker: Path, body: str = 'cat') -> Path:
    path.write_text(f'#!/bin/sh\ntouch "{marker}"\n{body}\n', encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def plant_clean_filter(workspace, marker: Path) -> None:
    """Hostile `.git` config that defines a clean filter, plus a `.gitattributes` that selects it."""
    plant(workspace, marker.with_name('unused'))
    tree = Path(workspace.path)
    filt = program(tree / '.git' / 'filter.sh', marker)
    with (tree / '.git' / 'config').open('a', encoding='utf-8') as handle:
        handle.write(f'[filter "x"]\n\tclean = {filt}\n')
    (tree / '.gitattributes').write_text('* filter=x\n', encoding='utf-8')


def test_control_apply_in_a_tampered_worktree_runs_a_planted_clean_filter(project, tmp_path):
    ws, marker = make_workspace(project), tmp_path / 'ran'
    plant_clean_filter(ws, marker)
    PatchValidator().apply(PATCH, Path(ws.path))
    assert marker.exists()


def test_patch_apply_does_not_run_a_planted_clean_filter(project, tmp_path):
    """git apply never reads core.fsmonitor, so only a filter shows whether the apply calls are protected."""
    ws, marker = make_workspace(project), tmp_path / 'ran'
    plant_clean_filter(ws, marker)
    PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    assert not marker.exists()


def test_patch_git_calls_ignore_a_filter_from_the_users_global_config(project, tmp_path, monkeypatch):
    """git-lfs style global filters could otherwise be chosen by a .gitattributes the container wrote."""
    ws, marker = make_workspace(project), tmp_path / 'ran'
    filt = program(tmp_path / 'global_filter.sh', marker)
    global_config = tmp_path / 'gitconfig'
    global_config.write_text(f'[filter "x"]\n\tclean = {filt}\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(global_config))
    (Path(ws.path) / '.gitattributes').write_text('* filter=x\n', encoding='utf-8')
    PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    assert not marker.exists()


@pytest.fixture
def gitlink_project(project):
    """A source repository whose base commit contains a gitlink (submodule entry)."""
    root, state, db = project
    head = run_git(root, 'rev-parse', 'HEAD').stdout.strip()
    run_git(root, 'update-index', '--add', '--cacheinfo', f'160000,{head},sub')
    run_git(root, 'commit', '-qm', 'add a gitlink')
    (root / 'sub').mkdir()  # an uninitialised submodule is an empty directory
    return root, state, db


def plant_nested_repository(workspace, marker: Path) -> None:
    """What a test command could do under a gitlink path, which the worktree leaves empty."""
    sub = Path(workspace.path) / 'sub'
    sub.mkdir(exist_ok=True)
    run_git(sub, 'init', '-q')
    filt = program(sub / 'filter.sh', marker)
    run_git(sub, 'config', 'filter.x.clean', str(filt))
    (sub / '.gitattributes').write_text('* filter=x\n', encoding='utf-8')
    (sub / 'f.txt').write_text('hello\n', encoding='utf-8')
    run_git(sub, 'add', 'f.txt', '.gitattributes')
    os.utime(sub / 'f.txt', (1, 1))
    marker.unlink(missing_ok=True)


def test_control_git_status_runs_the_clean_filter_of_a_nested_repository_unless_submodules_are_ignored(
        gitlink_project, tmp_path):
    ws, marker = make_workspace(gitlink_project), tmp_path / 'ran'
    plant_nested_repository(ws, marker)
    _git(Path(ws.path), 'status', '--porcelain', '--untracked-files=all', git_dir=worktree_git_dir(ws))
    assert marker.exists()


def test_the_post_test_status_does_not_run_a_nested_repositorys_clean_filter(gitlink_project, tmp_path):
    ws, marker = make_workspace(gitlink_project), tmp_path / 'ran'
    inspection = PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    plant_nested_repository(ws, marker)
    _, diff = _changed_diff(ws, inspection)
    assert not marker.exists()
    assert 'VALUE = 2' in diff


def test_a_trusted_configs_own_fsmonitor_is_switched_off_for_host_git(project, tmp_path):
    """The source repository's config is trusted, but its builtin fsmonitor should not run for this check."""
    ws, marker = make_workspace(project), tmp_path / 'ran'
    monitor = program(tmp_path / 'monitor.sh', marker, 'printf "\\0"')
    run_git(Path(ws.repository_root), 'config', 'core.fsmonitor', str(monitor))
    inspection = PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    marker.unlink(missing_ok=True)
    _changed_diff(ws, inspection)
    assert not marker.exists()
    raw = subprocess.run(['git', '--git-dir', str(worktree_git_dir(ws)), '--work-tree', ws.path, 'status'],
                         cwd=ws.path, capture_output=True, env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull))
    assert raw.returncode == 0 and marker.exists()  # control: without the flag the same config runs it


def test_pathspecs_are_taken_literally(project):
    ws = make_workspace(project)
    git_dir = worktree_git_dir(ws)
    assert _git(Path(ws.path), 'ls-files', '--', '*.py', git_dir=git_dir) == ''
    assert _git(Path(ws.path), 'ls-files', '--', '*.py') == 'app.py'


def test_a_relative_gitdir_pointer_is_resolved_from_its_own_directory(project, tmp_path, monkeypatch):
    ws = make_workspace(project)
    entry = worktree_git_dir(ws)
    (entry / 'gitdir').write_text(os.path.relpath(Path(ws.path).resolve() / '.git', entry) + '\n',
                                  encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    assert worktree_git_dir(ws) == entry


def test_a_missing_worktree_is_a_workspace_error_not_a_bare_os_error(project, tmp_path):
    ws = make_workspace(project)
    gone = Workspace(str(tmp_path / 'nowhere'), ws.repository_root, ws.base_commit, ws.run_id)
    with pytest.raises(WorkspaceError):
        worktree_git_dir(gone)


def test_the_git_entry_check_accepts_only_the_original_pointer_file(project, tmp_path):
    ws = make_workspace(project)
    git_dir = worktree_git_dir(ws)
    entry = Path(ws.path) / '.git'
    assert git_entry_is_original(ws, git_dir)
    original = entry.read_text(encoding='utf-8')
    entry.write_text(f'gitdir: {tmp_path}/elsewhere\n', encoding='utf-8')
    assert not git_entry_is_original(ws, git_dir)
    entry.write_text('not a pointer\n', encoding='utf-8')
    assert not git_entry_is_original(ws, git_dir)
    entry.unlink()
    entry.symlink_to(git_dir)
    assert not git_entry_is_original(ws, git_dir)
    entry.unlink()
    assert not git_entry_is_original(ws, git_dir)
    entry.mkdir()
    assert not git_entry_is_original(ws, git_dir)
    entry.rmdir()
    entry.write_text(original, encoding='utf-8')
    assert git_entry_is_original(ws, git_dir)


def test_a_core_worktree_in_the_source_config_does_not_move_the_work_tree(project, tmp_path):
    """--work-tree is passed explicitly, so a core.worktree setting cannot point host Git somewhere else."""
    ws = make_workspace(project)
    other = tmp_path / 'other'
    other.mkdir()
    run_git(Path(ws.repository_root), 'config', 'core.worktree', str(other))
    inspection = PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    _, diff = _changed_diff(ws, inspection)
    assert 'VALUE = 2' in diff


def test_patch_git_calls_forward_the_users_line_ending_settings(project, monkeypatch):
    """The scrubbed environment hides the system autocrlf of Git for Windows, so it is passed explicitly."""
    ws = make_workspace(project)
    run_git(Path(ws.repository_root), 'config', 'core.autocrlf', 'true')
    calls, real = [], subprocess.run
    monkeypatch.setattr('decisionbound.code_patches.subprocess.run',
                        lambda argv, *a, **kw: calls.append(list(argv)) or real(argv, *a, **kw))
    PatchValidator().apply(PATCH, Path(ws.path), git_dir=worktree_git_dir(ws))
    patch_calls = [argv for argv in calls if 'apply' in argv or 'ls-files' in argv]
    assert len(patch_calls) == 3 and all('core.autocrlf=true' in argv for argv in patch_calls)
