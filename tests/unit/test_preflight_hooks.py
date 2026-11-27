"""The local preflight, the commit-msg hook and the hook installer do what they say."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
API = 'site_docs/api-reference.md'
HUMAN = {'GIT_AUTHOR_NAME': 'Ahmed Jawed', 'GIT_AUTHOR_EMAIL': '80323861+ahmedjawedaj@users.noreply.github.com',
         'GIT_COMMITTER_NAME': 'Ahmed Jawed', 'GIT_COMMITTER_EMAIL': '80323861+ahmedjawedaj@users.noreply.github.com'}
needs_sh = pytest.mark.skipif(shutil.which('sh') is None, reason='the hooks are sh scripts and sh is not on PATH')


def git(cwd, *args, env=None):
    full = {**os.environ, **HUMAN, 'GIT_CONFIG_NOSYSTEM': '1', **(env or {})}
    done = subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=cwd, env=full,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def run(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, env={**os.environ, **HUMAN, **(env or {})},
                          capture_output=True, text=True)


def commit_msg(tmp_path, message, env=None):
    path = tmp_path / 'MSG'
    path.write_text(message, encoding='utf-8')
    return run(['sh', str(REPO / '.githooks' / 'commit-msg'), str(path)], REPO, env)


@pytest.fixture
def checkout(tmp_path):
    """A throwaway git repository that holds a copy of the scripts, the sources and the API reference."""
    root = tmp_path / 'repo'
    shutil.copytree(REPO / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO / 'src' / 'decisionbound', root / 'src' / 'decisionbound',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (root / 'site_docs').mkdir()
    shutil.copy(REPO / API, root / API)
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '.')
    git(root, 'commit', '-q', '-m', 'Start')
    return root


def preflight(root, *args):
    return run([sys.executable, str(root / 'scripts' / 'preflight.py'), *args], root)


@needs_sh
@pytest.mark.parametrize('trailer', [
    'Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_abc',
    'Generated with [Claude Code](https://claude.com/claude-code)',
])
def test_commit_msg_hook_rejects_assistant_markers(tmp_path, trailer):
    done = commit_msg(tmp_path, f'Fix the thing\n\n{trailer}\n')
    assert done.returncode == 1
    assert 'Commit refused' in done.stderr


@needs_sh
def test_commit_msg_hook_accepts_a_clean_message_and_a_plain_mention(tmp_path):
    assert commit_msg(tmp_path, 'Fix the thing\n\nExplains why.\n').returncode == 0
    assert commit_msg(tmp_path, 'Add a Claude adapter\n\nThe adapter calls the Anthropic API.\n').returncode == 0


@needs_sh
def test_commit_msg_hook_ignores_comment_lines_like_git(tmp_path):
    message = 'Fix the thing\n\n# Last session was at https://claude.ai/code/session_abc\n'
    assert commit_msg(tmp_path, message).returncode == 0


@needs_sh
def test_commit_msg_hook_ignores_the_verbose_diff_after_the_scissors_line(tmp_path):
    message = ('Fix the thing\n\n# ------------------------ >8 ------------------------\n'
               'diff --git a/NOTES.md b/NOTES.md\n'
               '+Generated with [Claude Code](https://claude.com/claude-code)\n')
    assert commit_msg(tmp_path, message).returncode == 0


@needs_sh
def test_commit_msg_hook_rejects_an_assistant_author(tmp_path):
    done = commit_msg(tmp_path, 'Fix the thing\n', {'GIT_AUTHOR_NAME': 'Claude', 'GIT_AUTHOR_EMAIL': 'noreply@anthropic.com'})
    assert done.returncode == 1
    assert 'assistant identity' in done.stderr


def test_preflight_passes_on_a_matching_api_reference(checkout):
    done = preflight(checkout, '--only', 'api-reference')
    assert done.returncode == 0, done.stdout + done.stderr


def test_preflight_names_the_api_reference_step_when_it_drifts(checkout):
    target = checkout / API
    original = target.read_text(encoding='utf-8')
    target.write_text(original + '\nA hand edit that the generator would not write.\n', encoding='utf-8')
    git(checkout, 'commit', '-q', '-am', 'Drift the API reference')
    done = preflight(checkout, '--only', 'api-reference')
    assert done.returncode == 1
    assert 'Preflight failed at step "api-reference"' in done.stdout
    assert 'Commit the regenerated site_docs/api-reference.md' in done.stdout
    assert 'left in place' in done.stdout
    assert target.read_text(encoding='utf-8') == original


def test_preflight_stops_at_the_first_failing_step(checkout):
    (checkout / 'docs').mkdir()
    (checkout / 'docs' / 'bad.html').write_text('<a href="missing.html">x</a>', encoding='utf-8')
    done = preflight(checkout, '--only', 'docs', '--only', 'api-reference')
    assert done.returncode == 1
    assert 'Preflight failed at step "docs"' in done.stdout
    assert '[2/2] api-reference' not in done.stdout


def test_preflight_runs_the_real_attribution_check_and_fast_skips_it(checkout):
    base = git(checkout, 'rev-parse', 'HEAD')
    (checkout / 'note.txt').write_text('x', encoding='utf-8')
    git(checkout, 'add', 'note.txt')
    git(checkout, 'commit', '-q', '-m', 'Add a note\n\nCo-Authored-By: Claude <noreply@anthropic.com>')
    done = preflight(checkout, '--only', 'attribution', '--base', base)
    assert done.returncode == 1
    assert 'Preflight failed at step "attribution"' in done.stdout
    assert 'Co-Authored-By trailer' in done.stdout
    fast = preflight(checkout, '--only', 'attribution', '--base', base, '--fast')
    assert fast.returncode == 0
    assert 'skipped (--fast)' in fast.stdout


def test_preflight_says_so_when_the_commit_history_cannot_be_read(checkout):
    done = preflight(checkout, '--only', 'attribution', '--base', 'no-such-revision')
    assert done.returncode == 1
    assert 'Preflight failed at step "attribution"' in done.stdout
    assert 'could not read the commit history' in done.stdout
    assert 'Rewrite the listed commits' not in done.stdout


def test_install_hooks_sets_and_unsets_core_hooks_path(tmp_path):
    repo = tmp_path / 'repo'
    (repo / '.githooks').mkdir(parents=True)
    git(repo, 'init', '-q')
    script = str(REPO / 'scripts' / 'install_hooks.py')

    def configured():
        done = subprocess.run(['git', 'config', '--local', '--get', 'core.hooksPath'], cwd=repo, capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else None

    assert configured() is None
    done = run([sys.executable, script, '--repo', str(repo)], tmp_path)
    assert done.returncode == 0, done.stderr
    assert 'Set core.hooksPath' in done.stdout
    assert configured() == '.githooks'
    done = run([sys.executable, script, '--repo', str(repo), '--uninstall'], tmp_path)
    assert done.returncode == 0, done.stderr
    assert 'Unset core.hooksPath' in done.stdout
    assert configured() is None


def test_install_hooks_leaves_someone_elses_hooks_path_alone(tmp_path):
    repo = tmp_path / 'repo'
    (repo / '.githooks').mkdir(parents=True)
    git(repo, 'init', '-q')
    git(repo, 'config', '--local', 'core.hooksPath', 'my-hooks')
    script = str(REPO / 'scripts' / 'install_hooks.py')
    assert run([sys.executable, script, '--repo', str(repo)], tmp_path).returncode == 1
    assert git(repo, 'config', '--local', 'core.hooksPath') == 'my-hooks'
    assert run([sys.executable, script, '--repo', str(repo), '--uninstall'], tmp_path).returncode == 1
    assert git(repo, 'config', '--local', 'core.hooksPath') == 'my-hooks'
