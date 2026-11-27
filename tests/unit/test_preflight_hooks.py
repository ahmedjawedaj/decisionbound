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


@pytest.fixture
def checkout(tmp_path):
    """A throwaway git repository that holds a copy of the scripts, the sources and the API reference."""
    root = tmp_path / 'repo'
    shutil.copytree(REPO / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO / 'src' / 'decisionbound', root / 'src' / 'decisionbound',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO / '.githooks', root / '.githooks')
    (root / 'site_docs').mkdir()
    shutil.copy(REPO / API, root / API)
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '.')
    git(root, 'commit', '-q', '-m', 'Start')
    return root


def preflight(root, *args, env=None):
    return run([sys.executable, str(root / 'scripts' / 'preflight.py'), *args], root, env)


def commit_msg(checkout, message, env=None):
    path = checkout / 'MSG'
    path.write_text(message, encoding='utf-8')
    return run(['sh', str(checkout / '.githooks' / 'commit-msg'), str(path)], checkout, env)


def install(checkout, *args, env=None):
    return run([sys.executable, str(checkout / 'scripts' / 'install_hooks.py'), '--repo', str(checkout), *args],
               checkout, env)


EDITOR_HELP = ('\n# Please enter the commit message for your changes. Lines starting\n'
               "# with '#' will be ignored, and an empty message aborts the commit.\n")


@needs_sh
@pytest.mark.parametrize('trailer', [
    'Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_abc',
    'Generated with [Claude Code](https://claude.com/claude-code)',
])
def test_commit_msg_hook_rejects_assistant_markers(checkout, trailer):
    done = commit_msg(checkout, f'Fix the thing\n\n{trailer}\n')
    assert done.returncode == 1
    assert 'Commit refused' in done.stderr


@needs_sh
def test_commit_msg_hook_accepts_a_clean_message_and_a_plain_mention(checkout):
    assert commit_msg(checkout, 'Fix the thing\n\nExplains why.\n').returncode == 0
    assert commit_msg(checkout, 'Add a Claude adapter\n\nThe adapter calls the Anthropic API.\n').returncode == 0


@needs_sh
def test_commit_msg_hook_checks_comment_lines_in_a_message_given_with_m(checkout):
    """Git keeps these lines when the message comes from -m or -F, so they are checked."""
    message = 'Fix the thing\n\n# Generated with [Claude Code](https://claude.com/claude-code)\n'
    assert commit_msg(checkout, message).returncode == 1
    tail = ('Fix the thing\n\n# ------------------------ >8 ------------------------\n'
            'Co-Authored-By: Claude <noreply@anthropic.com>\n')
    assert commit_msg(checkout, tail).returncode == 1


@needs_sh
def test_commit_msg_hook_ignores_comments_and_the_verbose_diff_in_an_editor_file(checkout):
    """Only a file that holds Git's own editor help text is cleaned the way Git cleans it."""
    message = ('Fix the thing\n' + EDITOR_HELP + '# Last session was at https://claude.ai/code/session_abc\n'
               '# ------------------------ >8 ------------------------\n'
               'diff --git a/NOTES.md b/NOTES.md\n'
               '+Generated with [Claude Code](https://claude.com/claude-code)\n')
    assert commit_msg(checkout, message).returncode == 0
    assert commit_msg(checkout, message.replace('Fix the thing\n', 'Fix the thing\n\nCo-Authored-By: Claude <a@b.c>\n', 1)).returncode == 1


@needs_sh
def test_installed_commit_msg_hook_blocks_a_real_commit_with_a_hidden_trailer(checkout):
    assert install(checkout).returncode == 0
    (checkout / 'note.txt').write_text('x', encoding='utf-8')
    git(checkout, 'add', 'note.txt')
    refused = run(['git', 'commit', '-q', '-m', 'Fix 2', '-m', '# ------ >8 ------\nCo-Authored-By: Claude <noreply@anthropic.com>'], checkout)
    assert refused.returncode == 1 and 'Commit refused' in refused.stderr
    assert run(['git', 'commit', '-q', '-m', 'Add a note'], checkout).returncode == 0


@needs_sh
def test_commit_msg_hook_rejects_an_assistant_author_and_committer(checkout):
    done = commit_msg(checkout, 'Fix the thing\n', {'GIT_AUTHOR_NAME': 'Claude', 'GIT_AUTHOR_EMAIL': 'noreply@anthropic.com'})
    assert done.returncode == 1
    assert 'assistant identity' in done.stderr
    done = commit_msg(checkout, 'Fix the thing\n', {'GIT_COMMITTER_NAME': 'Claude', 'GIT_COMMITTER_EMAIL': 'noreply@anthropic.com'})
    assert done.returncode == 1 and 'committer' in done.stderr.lower()


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


def test_preflight_refuses_the_api_reference_step_while_the_source_is_dirty(checkout):
    ports = checkout / 'src' / 'decisionbound' / 'ports.py'
    ports.write_text(ports.read_text(encoding='utf-8') + '\n# an uncommitted edit\n', encoding='utf-8')
    before = (checkout / API).read_text(encoding='utf-8')
    done = preflight(checkout, '--only', 'api-reference')
    assert done.returncode == 1
    assert 'uncommitted changes' in done.stdout and 'ports.py' in done.stdout
    assert (checkout / API).read_text(encoding='utf-8') == before  # nothing was rewritten


def test_preflight_wants_the_regenerated_reference_committed_not_just_staged(checkout):
    target = checkout / API
    good = target.read_text(encoding='utf-8')
    target.write_text(good + '\nA stale line.\n', encoding='utf-8')
    git(checkout, 'commit', '-q', '-am', 'Commit a stale reference')
    target.write_text(good, encoding='utf-8')
    git(checkout, 'add', API)  # the right file is staged, but HEAD still holds the stale one
    done = preflight(checkout, '--only', 'api-reference')
    assert done.returncode == 1 and 'differs from HEAD' in done.stdout


def test_preflight_fails_on_an_untracked_api_reference(checkout):
    git(checkout, 'rm', '-q', '--cached', API)
    done = preflight(checkout, '--only', 'api-reference')
    assert done.returncode == 1 and 'is not tracked by git' in done.stdout


def test_preflight_lists_steps_and_rejects_an_unknown_step(checkout):
    listed = preflight(checkout, '--list')
    assert listed.stdout.split() == ['publication', 'docs', 'api-reference', 'attribution']
    done = preflight(checkout, '--only', 'nope')
    assert done.returncode == 2 and 'Unknown step: nope' in done.stderr


def test_preflight_finds_main_as_the_default_base_and_ignores_pr_variables(checkout):
    git(checkout, 'checkout', '-q', '-b', 'topic')
    (checkout / 'note.txt').write_text('x', encoding='utf-8')
    git(checkout, 'add', 'note.txt')
    git(checkout, 'commit', '-q', '-m', 'Add a note')
    env = {'PR_TITLE': 'Add Co-Authored-By: Claude <noreply@anthropic.com>', 'PR_BODY': 'Generated with [Claude Code](x)'}
    assert preflight(checkout, '--only', 'attribution', env=env).returncode == 0
    (checkout / 'more.txt').write_text('y', encoding='utf-8')
    git(checkout, 'add', 'more.txt')
    git(checkout, 'commit', '-q', '-m', 'More\n\nCo-Authored-By: Claude <noreply@anthropic.com>')
    assert preflight(checkout, '--only', 'attribution').returncode == 1
    clean = git(checkout, 'rev-parse', 'HEAD~1')
    assert preflight(checkout, '--only', 'attribution', '--head', clean).returncode == 0


def test_preflight_says_how_to_get_a_missing_base(checkout):
    git(checkout, 'branch', '-m', 'work')
    done = preflight(checkout, '--only', 'attribution')
    assert done.returncode == 1 and 'git fetch origin main' in done.stdout


@pytest.fixture
def pushing(checkout, tmp_path):
    """A checkout with the hooks installed and a bare remote, plus a branch that carries a Claude trailer."""
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '-q', '--bare', str(remote))
    git(checkout, 'remote', 'add', 'origin', str(remote))
    git(checkout, 'push', '-q', 'origin', 'main')
    git(checkout, 'checkout', '-q', '-b', 'bad')
    (checkout / 'bad.txt').write_text('x', encoding='utf-8')
    git(checkout, 'add', 'bad.txt')
    git(checkout, 'commit', '-q', '-m', 'Bad\n\nCo-Authored-By: Claude <noreply@anthropic.com>')
    git(checkout, 'checkout', '-q', 'main')
    assert install(checkout).returncode == 0
    return checkout


@needs_sh
def test_pre_push_hook_checks_the_ref_being_pushed_not_the_checked_out_one(pushing):
    refused = run(['git', 'push', 'origin', 'bad'], pushing)
    assert refused.returncode != 0 and 'Co-Authored-By trailer' in refused.stdout + refused.stderr
    assert 'not checked out' in refused.stdout + refused.stderr
    git(pushing, 'checkout', '-q', 'bad')
    allowed = run(['git', 'push', 'origin', 'main'], pushing)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr  # the pushed ref is clean


@needs_sh
def test_pre_push_hook_lets_a_delete_only_push_through(pushing):
    git(pushing, 'push', '-q', '--no-verify', 'origin', 'main:old')
    done = run(['git', 'push', 'origin', '--delete', 'old'], pushing)
    assert done.returncode == 0, done.stdout + done.stderr


def test_install_hooks_refuses_while_it_would_switch_off_a_hook_you_have(checkout):
    hook = checkout / '.git' / 'hooks' / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    done = install(checkout)
    assert done.returncode == 1 and 'pre-commit' in done.stderr
    assert subprocess.run(['git', 'config', '--local', '--get', 'core.hooksPath'], cwd=checkout).returncode == 1
    assert install(checkout, '--force').returncode == 0


def test_install_hooks_refuses_while_a_global_hooks_path_exists(checkout, tmp_path):
    cfg = tmp_path / 'global.cfg'
    cfg.write_text('[core]\n\thooksPath = /somewhere/hooks\n', encoding='utf-8')
    done = install(checkout, env={'GIT_CONFIG_GLOBAL': str(cfg)})
    assert done.returncode == 1 and 'global' in done.stderr


def test_install_hooks_puts_back_a_value_it_replaced(checkout):
    git(checkout, 'config', '--local', 'core.hooksPath', '.husky/_')
    assert install(checkout).returncode == 1
    assert install(checkout, '--force').returncode == 0
    assert git(checkout, 'config', '--local', 'core.hooksPath') == '.githooks'
    done = install(checkout, '--uninstall')
    assert done.returncode == 0 and 'Restored' in done.stdout
    assert git(checkout, 'config', '--local', 'core.hooksPath') == '.husky/_'


def test_install_hooks_will_not_act_on_a_folder_inside_another_repository(checkout):
    inner = checkout / 'inner'
    (inner / '.githooks').mkdir(parents=True)
    done = run([sys.executable, str(checkout / 'scripts' / 'install_hooks.py'), '--repo', str(inner)], checkout)
    assert done.returncode == 2 and 'not the root' in done.stderr
    assert subprocess.run(['git', 'config', '--local', '--get', 'core.hooksPath'], cwd=checkout).returncode == 1


def test_preflight_starts_after_origin_main_before_local_main(checkout, tmp_path):
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '-q', '--bare', str(remote))
    git(checkout, 'remote', 'add', 'origin', str(remote))
    git(checkout, 'push', '-q', 'origin', 'main')
    (checkout / 'late.txt').write_text('x', encoding='utf-8')
    git(checkout, 'add', 'late.txt')
    git(checkout, 'commit', '-q', '-m', 'Late\n\nCo-Authored-By: Claude <noreply@anthropic.com>')
    done = preflight(checkout, '--only', 'attribution')  # local main already holds the bad commit
    assert done.returncode == 1 and 'Co-Authored-By trailer' in done.stdout
