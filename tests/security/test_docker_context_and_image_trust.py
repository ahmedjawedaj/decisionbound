"""The sandbox must run on the daemon the operator meant and must record which image it ran."""
from __future__ import annotations
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from decisionbound.code_models import TestOutcome, Workspace
from decisionbound.code_sandbox import DockerTestRunner
from tests.docker_fakes import cli_result

DIGEST = 'sha256:' + 'a' * 64
PINNED = 'python@' + DIGEST


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DBOUND_REQUIRE_IMAGE_DIGEST'):
        monkeypatch.delenv(name, raising=False)


def workspace(tmp_path: Path, name: str = 'isolated') -> Workspace:
    path = tmp_path / name
    path.mkdir()
    return Workspace(str(path), str(tmp_path), 'a' * 40, 'code_' + '1' * 32)


def captured_run(tmp_path, monkeypatch, runner, result=None):
    seen = []
    monkeypatch.setattr('decisionbound.code_sandbox._run_cli',
                        lambda argv, env, timeout: seen.append((argv, env)) or (result or cli_result(0)))
    receipt = runner.run(workspace(tmp_path), ('python', '-V'), 5)
    return receipt, seen


# Docker context trust

FAKE_DOCKER = '''#!{python}
import json, os, sys
env = os.environ
config_dir = env.get('DOCKER_CONFIG') or os.path.join(env.get('HOME', ''), '.docker')
config = {{}}
try:
    with open(os.path.join(config_dir, 'config.json')) as handle:
        config = json.load(handle)
except OSError:
    pass
# The documented order: DOCKER_HOST, then DOCKER_CONTEXT, then the context saved by `docker context use`.
if env.get('DOCKER_HOST'):
    context = 'default'
elif env.get('DOCKER_CONTEXT'):
    context = env['DOCKER_CONTEXT']
else:
    context = config.get('currentContext') or 'default'
with open({record!r}, 'a') as out:
    out.write(json.dumps({{'argv': sys.argv[1:], 'context': context,
                           'env': {{k: v for k, v in env.items() if k.startswith('DOCKER')}}}}) + '\\n')
'''


@pytest.fixture
def fake_docker_binary(tmp_path, monkeypatch):
    """A `docker` first on PATH that records argv and DOCKER* variables, and picks its context the way
    the real CLI documents it. It models that order and is not the real CLI."""
    if sys.platform == 'win32':
        pytest.skip('the fake CLI is a POSIX script')
    bin_dir, record = tmp_path / 'bin', tmp_path / 'record.jsonl'
    bin_dir.mkdir()
    script = bin_dir / 'docker'
    script.write_text(FAKE_DOCKER.format(python=sys.executable, record=str(record)))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / 'home'
    (home / '.docker').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    def calls():
        return [json.loads(line) for line in record.read_text().splitlines()]
    return home, calls


def test_a_remote_context_saved_in_the_docker_config_is_not_followed(tmp_path, monkeypatch, fake_docker_binary):
    home, calls = fake_docker_binary
    (home / '.docker' / 'config.json').write_text(json.dumps({'currentContext': 'remote-ssh-host'}))
    DockerTestRunner(PINNED).run(workspace(tmp_path), ('python', '-V'), 5)
    assert calls()[0]['context'] == 'default'


def test_docker_variables_of_the_caller_are_not_passed_on(tmp_path, monkeypatch, fake_docker_binary):
    """Holds on the old code too. Only the pinned context and DOCKER_HOST may reach the CLI."""
    home, calls = fake_docker_binary
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote-ssh-host')
    monkeypatch.setenv('DOCKER_CONFIG', str(tmp_path / 'other-config'))
    monkeypatch.setenv('DOCKER_TLS_VERIFY', '0')
    monkeypatch.setenv('DOCKER_CERT_PATH', '/somewhere')
    DockerTestRunner(PINNED).run(workspace(tmp_path), ('python', '-V'), 5)
    assert set(calls()[0]['env']) <= {'DOCKER_CONTEXT'}


def test_a_context_variable_of_the_caller_does_not_choose_the_daemon(tmp_path, monkeypatch, fake_docker_binary):
    home, calls = fake_docker_binary
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote-ssh-host')
    DockerTestRunner(PINNED).run(workspace(tmp_path), ('python', '-V'), 5)
    assert calls()[0]['context'] == 'default'


def test_a_local_unix_daemon_still_works_and_needs_no_context_pin(tmp_path, monkeypatch, fake_docker_binary):
    home, calls = fake_docker_binary
    (home / '.docker' / 'config.json').write_text(json.dumps({'currentContext': 'remote-ssh-host'}))
    monkeypatch.setenv('DOCKER_HOST', 'unix:///run/user/1000/docker.sock')
    DockerTestRunner(PINNED).run(workspace(tmp_path), ('python', '-V'), 5)
    seen = calls()[0]
    assert seen['env'] == {'DOCKER_HOST': 'unix:///run/user/1000/docker.sock'}
    assert seen['context'] == 'default'


def test_the_cleanup_command_runs_on_the_same_pinned_daemon(tmp_path, monkeypatch):
    envs = []
    monkeypatch.setattr('decisionbound.code_sandbox._run_cli',
                        lambda argv, env, timeout: cli_result(None, timed_out=True))
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run',
                        lambda argv, **kw: envs.append(kw['env']) or subprocess.CompletedProcess(argv, 0, b'', b''))
    DockerTestRunner(PINNED).run(workspace(tmp_path), ('python', '-V'), 1)
    assert envs and envs[0].get('DOCKER_CONTEXT') == 'default'


def test_podman_gets_no_docker_variables_of_its_own(tmp_path, monkeypatch):
    _, seen = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED, docker_binary='podman'))
    assert not [name for name in seen[0][1] if name.startswith('DOCKER')]


def test_the_receipt_names_the_daemon_endpoint(tmp_path, monkeypatch):
    receipt, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED))
    assert receipt.daemon_endpoint == 'default'
    monkeypatch.setenv('DOCKER_HOST', 'unix:///run/user/1000/docker.sock')
    receipt = DockerTestRunner(PINNED).run(workspace(tmp_path, 'second'), ('python', '-V'), 5)
    assert receipt.daemon_endpoint == 'unix:///run/user/1000/docker.sock'


# Image trust

def test_a_digest_reference_is_recorded_in_the_receipt_and_argv_is_unchanged(tmp_path, monkeypatch):
    receipt, seen = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED))
    argv = seen[0][0]
    assert receipt.image == PINNED and receipt.image_digest == DIGEST
    assert argv[argv.index('--pull') + 1] == 'never'
    assert argv[argv.index(PINNED) - 1] == '/workspace'


def test_a_tag_is_recorded_as_unpinned_with_no_digest(tmp_path, monkeypatch):
    receipt, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner('python:3.12'))
    assert receipt.image == 'python:3.12' and receipt.image_digest == ''


def test_a_bare_image_id_counts_as_pinned(tmp_path, monkeypatch):
    receipt, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner(DIGEST))
    assert receipt.image_digest == DIGEST


def test_a_stopped_run_receipt_also_names_the_image(tmp_path, monkeypatch):
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run',
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b'', b''))
    receipt, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED),
                              cli_result(None, timed_out=True))
    assert receipt.timed_out and receipt.image_digest == DIGEST and receipt.daemon_endpoint == 'default'


def test_strict_mode_refuses_a_tag_before_anything_runs():
    with pytest.raises(ValueError, match='sha256 digest'):
        DockerTestRunner('python:3.12', require_digest=True)


@pytest.mark.parametrize('image', ['python@sha256:abc', 'python@sha256:' + 'A' * 64, 'python@sha512:' + 'a' * 64,
                                   'python:3.12@sha256:' + 'a' * 63, 'sha256:' + 'a' * 65])
def test_strict_mode_refuses_a_malformed_digest(image):
    with pytest.raises(ValueError, match='sha256 digest'):
        DockerTestRunner(image, require_digest=True)


def test_strict_mode_accepts_a_digest_reference_and_a_tag_with_a_digest():
    DockerTestRunner(PINNED, require_digest=True)
    DockerTestRunner('python:3.12@' + DIGEST, require_digest=True)


def test_the_environment_switch_turns_strict_mode_on(monkeypatch):
    monkeypatch.setenv('DBOUND_REQUIRE_IMAGE_DIGEST', '1')
    with pytest.raises(ValueError, match='sha256 digest'):
        DockerTestRunner('python:3.12')
    DockerTestRunner(PINNED)


def test_the_argument_cannot_loosen_the_environment_switch(monkeypatch):
    monkeypatch.setenv('DBOUND_REQUIRE_IMAGE_DIGEST', '1')
    with pytest.raises(ValueError, match='sha256 digest'):
        DockerTestRunner('python:3.12', require_digest=False)


def test_a_tag_is_still_allowed_by_default():
    DockerTestRunner('python:3.12')


# Receipt compatibility

OLD_FIELDS = {'kind', 'command', 'exit_code', 'timed_out', 'duration_ms', 'stdout_sha256', 'stderr_sha256',
              'output_truncated'}


def test_a_receipt_without_image_information_serializes_exactly_as_before():
    old = TestOutcome('mock', ('python',), 0, False, 0, 'a' * 64, 'b' * 64)
    assert set(old.to_dict()) == OLD_FIELDS


def test_a_docker_receipt_adds_only_the_new_fields(tmp_path, monkeypatch):
    receipt, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED))
    assert set(receipt.to_dict()) == OLD_FIELDS | {'image', 'image_digest', 'daemon_endpoint'}


def test_the_receipt_hash_binds_the_image(tmp_path, monkeypatch):
    import dataclasses
    from decisionbound.code_execution import _test_receipt_hash
    first, _ = captured_run(tmp_path, monkeypatch, DockerTestRunner(PINNED))
    second = DockerTestRunner('python@sha256:' + 'b' * 64).run(workspace(tmp_path, 'second'), ('python', '-V'), 5)
    second = dataclasses.replace(second, duration_ms=first.duration_ms)
    assert _test_receipt_hash(second) != _test_receipt_hash(first)
