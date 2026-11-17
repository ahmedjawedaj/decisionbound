"""Release publication must not execute unreviewed code or reuse a version."""
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/verify_pypi_release.py'
spec = importlib.util.spec_from_file_location('pypi_release', SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SHA = 'a' * 40


@pytest.fixture
def tagged_repo(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    def git(*args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        return subprocess.check_output(['git', '-C', str(root), '-c', 'user.name=Release test',
                                       '-c', 'user.email=release@example.invalid', '-c', 'commit.gpgSign=false',
                                       '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=' + str(tmp_path / 'no-hooks'),
                                       *args], env=env, text=True).strip()
    git('init', '-b', 'main')
    (root / 'src/decisionbound').mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname = "decisionbound"\nversion = "0.13.0a1"\n')
    (root / 'src/decisionbound/__init__.py').write_text('__version__ = "0.13.0a1"\nraise RuntimeError("Tagged code must never be imported")\n')
    (root / 'CITATION.cff').write_text('version: 0.13.0a1\n')
    git('add', '.')
    git('commit', '-m', 'Reviewed version')
    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    git('tag', 'v0.13.0a1')
    return root, git


def test_reads_metadata_without_executing_tagged_package(tagged_repo):
    root, git = tagged_repo
    assert release.verify_source(root, 'v0.13.0a1') == (git('rev-parse', 'HEAD'), '0.13.0a1')


@pytest.mark.parametrize('tag', ['main', 'v0.13', 'v1.0.0+local', 'v1.0.0;id', '--help', 'v1.0.0\n'])
def test_rejects_unsafe_or_non_release_tags(tag):
    with pytest.raises(ValueError):
        release.validate_tag(tag)


@pytest.mark.parametrize('tag', ['v1.0.0', 'v1.0.0a1', 'v1.0.0b2', 'v1.0.0rc3', 'v1.0.0.post1', 'v1.0.0.dev2'])
def test_supports_stable_and_prerelease_versions(tag):
    assert release.validate_tag(tag) == tag[1:]


@pytest.mark.parametrize('path', ['pyproject.toml', 'src/decisionbound/__init__.py', 'CITATION.cff'])
def test_refuses_drift_in_any_version_metadata(tagged_repo, path):
    root, git = tagged_repo
    file = root / path
    file.write_text(file.read_text().replace('0.13.0a1', '0.12.0'))
    git('add', path)
    git('commit', '-m', 'Mismatched metadata')
    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    git('tag', '-f', 'v0.13.0a1')
    with pytest.raises(ValueError, match='must match'):
        release.verify_source(root, 'v0.13.0a1')


def test_refuses_a_tag_outside_reviewed_main(tagged_repo):
    root, git = tagged_repo
    git('checkout', '-b', 'unreviewed')
    (root / 'unreviewed.txt').write_text('Not part of main')
    git('add', '.')
    git('commit', '-m', 'Unreviewed work')
    git('tag', 'v0.14.0')
    with pytest.raises(subprocess.CalledProcessError):
        release.verify_source(root, 'v0.14.0')


def test_rejects_a_version_already_published():
    with pytest.raises(ValueError, match='already on PyPI'):
        release.require_unused_version('0.13.0a1', fetch=lambda url: {'info': {}})
    release.require_unused_version('0.13.0a1', fetch=lambda url: None)


def run(status='completed', conclusion='success', **changes):
    return dict(head_sha=SHA, head_branch='main', event='push', run_number=1,
                run_attempt=1, status=status, conclusion=conclusion, **changes)


def test_requires_all_three_exact_commit_main_workflows():
    requested = []
    def fetch(url, token):
        requested.append(url)
        return {'workflow_runs': [run()]}
    release.wait_for_certification('owner/repo', SHA, 'fake-test-token', fetch=fetch, wait_seconds=0)
    assert len(requested) == 3
    assert all('head_sha=' + SHA in url for url in requested)
    assert all(any(w in url for url in requested) for w in release.WORKFLOWS)


@pytest.mark.parametrize('field,value', [('head_sha', 'b' * 40), ('head_branch', 'feature'), ('event', 'pull_request')])
def test_other_commit_branch_or_event_cannot_certify(field, value):
    wrong = run()
    wrong[field] = value
    with pytest.raises(TimeoutError):
        release.wait_for_certification('owner/repo', SHA, 'fake', wait_seconds=0,
                                       fetch=lambda *args: {'workflow_runs': [wrong]})


def test_latest_failed_run_overrides_an_earlier_success():
    failed = run(conclusion='failure')
    failed['run_attempt'] = 2
    with pytest.raises(ValueError, match='certification failed'):
        release.wait_for_certification('owner/repo', SHA, 'fake', wait_seconds=0,
                                       fetch=lambda *args: {'workflow_runs': [run(), failed]})


def test_waits_for_pending_ci_then_continues_without_a_manual_rerun():
    elapsed = [0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    def fetch(*args):
        return {'workflow_runs': [run('in_progress', None) if elapsed[0] == 0 else run()]}
    release.wait_for_certification('owner/repo', SHA, 'fake', fetch=fetch,
                                   wait_seconds=120, clock=lambda: elapsed[0], sleep=sleep)
    assert sleeps == [60]


def test_missing_ci_times_out_without_publishing():
    with pytest.raises(TimeoutError):
        release.wait_for_certification('owner/repo', SHA, 'fake', wait_seconds=0,
                                       fetch=lambda *args: {'workflow_runs': []})
