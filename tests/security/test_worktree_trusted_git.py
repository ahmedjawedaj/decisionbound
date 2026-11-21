"""The host must not let Git read a `.git` entry that sandboxed test code could have replaced.

The Docker runner mounts the disposable worktree read/write, so a test command can delete the `.git`
file inside it and create a `.git` directory with its own config. Git would then read that config and
may run a program it names (here `core.fsmonitor`) as the host user. These tests plant such a directory
and check that no host-side Git call used by the coding workflow runs it.

POSIX only: the planted program is a shell script.
"""
from __future__ import annotations
import stat
import subprocess
import sys
from pathlib import Path
import pytest
from decisionbound.code_execution import PatchFileProposer, _changed_diff, run_coding_task
from decisionbound.code_patches import PatchValidator
from decisionbound.code_sandbox import ScriptedTestRunner
from decisionbound.code_workspace import GitWorktreeManager, WorkspaceError, _git, _line_ending_options, worktree_git_dir
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
    assert events['code_post_test_integrity']['unchanged'] is True
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
