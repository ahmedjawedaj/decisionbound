"""Docker backend must never accidentally execute untrusted tests on host."""
from __future__ import annotations
import subprocess
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from decisionbound.code_models import Workspace
from decisionbound.code_sandbox import DockerTestRunner, ScriptedTestRunner


@pytest.fixture(autouse=True)
def non_root_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)


def workspace(tmp_path: Path) -> Workspace:
    path = tmp_path/'isolated'
    path.mkdir()
    return Workspace(str(path), str(tmp_path), 'a'*40, 'code_'+'1'*32)


def test_docker_restrictions_are_argv_not_host_shell(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    requests = []
    def fake_run(argv, **kwargs):
        requests.append((argv,kwargs))
        return subprocess.CompletedProcess(argv, 0, b'test passed', b'')
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run', fake_run)
    receipt = DockerTestRunner('python@sha256:'+'a'*64).run(ws, ('python', '-m', 'pytest', '-q'), 15)
    assert receipt.kind == 'docker' and receipt.exit_code == 0
    command, options = requests[0]
    assert command[0] == 'docker'
    assert command[command.index('--network')+1] == 'none'
    assert 'ALL' in command and '--read-only' in command
    assert '--security-opt' in command and 'no-new-privileges' in command
    assert '--pull' in command and command[command.index('--pull')+1] == 'never'
    assert 'shell' not in options and command[-4:] == ['python', '-m', 'pytest', '-q']
    assert '--mount' in command and 'type=bind' in command[command.index('--mount')+1]


def test_root_caller_is_refused_before_any_daemon_process(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(os, 'getuid', lambda: 0, raising=False)
    with patch('decisionbound.code_sandbox.subprocess.run') as launch:
        with pytest.raises(PermissionError, match='refuses a root caller'):
            DockerTestRunner('python:3.12').run(ws, ('python', '-V'), 4)
    launch.assert_not_called()


@pytest.mark.parametrize('has_posix_ids', [True, False])
def test_container_uses_unprivileged_caller_or_platform_fallback(tmp_path, monkeypatch, has_posix_ids):
    ws = workspace(tmp_path)
    if has_posix_ids:
        monkeypatch.setattr(os, 'getuid', lambda: 1234, raising=False)
        monkeypatch.setattr(os, 'getgid', lambda: 5678, raising=False)
        expected = '1234:5678'
    else:
        monkeypatch.delattr(os, 'getuid', raising=False)
        monkeypatch.delattr(os, 'getgid', raising=False)
        expected = '1000:1000'
    with patch('decisionbound.code_sandbox.subprocess.run',
               return_value=subprocess.CompletedProcess([], 0, b'', b'')) as launch:
        DockerTestRunner('python:3.12').run(ws, ('python', '-V'), 4)
    argv = launch.call_args.args[0]
    assert argv[argv.index('--user') + 1] == expected


def test_docker_timeout_forces_exact_container_cleanup(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'run':
            raise subprocess.TimeoutExpired(argv, 6)
        return subprocess.CompletedProcess(argv, 0, b'', b'')
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run', fake_run)
    result = DockerTestRunner('python:3.12').run(ws, ('python', '-V'), 1)
    assert result.timed_out and result.exit_code is None
    assert calls[1][1:3] == ['rm', '-f']
    assert calls[1][3] == calls[0][calls[0].index('--name')+1]


def test_failed_container_cleanup_is_not_a_success_receipt(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    def fake_run(argv, **kwargs):
        if argv[1] == 'run':
            raise subprocess.TimeoutExpired(argv, 2)
        return subprocess.CompletedProcess(argv, 1, b'', b'denied')
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run', fake_run)
    with pytest.raises(RuntimeError, match='cleanup failed'):
        DockerTestRunner('python:3.12').run(ws, ('python','-V'), 1)


def test_remote_docker_daemon_denied(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setenv('DOCKER_HOST', 'tcp://example.com:2375')
    with pytest.raises(PermissionError, match='Remote Docker'):
        DockerTestRunner('python:3.12').run(ws, ('python', '-V'), 4)


def test_sandbox_requires_real_source_and_command_limits(tmp_path):
    ws = workspace(tmp_path)
    with pytest.raises(ValueError):
        DockerTestRunner('python:3.12').run(ws, ('',), 2)
    with pytest.raises(ValueError):
        DockerTestRunner('python:3.12').run(ws, ('python',), 0)
    with pytest.raises(ValueError):
        DockerTestRunner('-bad')
    with pytest.raises(ValueError):
        DockerTestRunner('python:3.12', memory_mb=100000)


def test_scripted_runner_is_honestly_mock_labeled(tmp_path):
    ws = workspace(tmp_path)
    result = ScriptedTestRunner([0]).run(ws, ('pytest',), 12)
    assert result.kind == 'mock'


# The real Docker probe in tests/integration/test_docker_isolation_real.py cannot see a bind of a
# tmpfs-backed host directory such as /dev/shm, because it looks like tmpfs from inside the container
# (issue 20). This check reads the argument list instead. It cannot see a mount that the daemon adds on
# its own, so it complements that probe and does not replace it.
HOST_ACCESS_FLAGS = ('-v', '--volume', '--volumes-from', '--device', '--privileged', '--cap-add',
                     '--pid', '--ipc', '--uts', '--userns', '--cgroupns')


def host_access_violations(argv: list[str], image: str, workspace_root: Path) -> list[str]:
    """Return every option before the image that could give the container host state or host namespaces."""
    head = argv[:argv.index(image)]
    problems, mounts, tmpfs = [], [], []
    for i, token in enumerate(head):
        name, has_value, inline = token.partition('=') if token.startswith('--') else (token, '', '')
        value = inline if has_value else (head[i + 1] if i + 1 < len(head) else '')
        if name in HOST_ACCESS_FLAGS or (token.startswith('-v') and not token.startswith('--')):
            problems.append(token)
        elif name == '--mount':
            mounts.append(value)
        elif name == '--tmpfs':
            tmpfs.append(value)
    if mounts != [f'type=bind,src={workspace_root},dst=/workspace']:
        problems.append(f'mounts are {mounts}')
    if len(tmpfs) != 1 or not tmpfs[0].startswith('/tmp:'):
        problems.append(f'tmpfs mounts are {tmpfs}')
    return problems


def real_argv(tmp_path, monkeypatch, command=('python', '-V')) -> tuple[list[str], str, Path]:
    ws = workspace(tmp_path)
    image = 'python@sha256:' + 'a' * 64
    seen = []
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run',
                        lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, b'', b''))
    DockerTestRunner(image).run(ws, command, 4)
    return seen[0], image, Path(ws.path).resolve()


def test_the_real_argument_list_gives_the_container_only_the_workspace_and_tmp(tmp_path, monkeypatch):
    argv, image, root = real_argv(tmp_path, monkeypatch)
    assert host_access_violations(argv, image, root) == []


@pytest.mark.parametrize('extra', [
    ['-v', '/dev/shm:/hostshm'],
    ['--volume=/dev/shm:/hostshm'],
    ['-v/dev/shm:/hostshm'],
    ['--volumes-from', 'other'],
    ['--mount', 'type=bind,src=/dev/shm,dst=/hostshm'],
    ['--mount', 'type=tmpfs,dst=/dev/shm'],
    ['--tmpfs', '/dev/shm'],
    ['--device', '/dev/mem'],
    ['--ipc', 'host'],
    ['--ipc=host'],
    ['--pid=host'],
    ['--uts', 'host'],
    ['--userns', 'host'],
    ['--cgroupns=host'],
    ['--privileged'],
    ['--cap-add', 'SYS_ADMIN'],
], ids=lambda e: ' '.join(e))
def test_control_an_extra_host_access_option_is_flagged(tmp_path, monkeypatch, extra):
    argv, image, root = real_argv(tmp_path, monkeypatch)
    at = argv.index(image)
    changed = argv[:at] + extra + argv[at:]
    assert host_access_violations(changed, image, root) != []


def test_control_a_missing_workspace_mount_is_flagged(tmp_path, monkeypatch):
    argv, image, root = real_argv(tmp_path, monkeypatch)
    at = argv.index('--mount')
    assert host_access_violations(argv[:at] + argv[at + 2:], image, root) != []


def test_control_a_workspace_mount_of_another_directory_is_flagged(tmp_path, monkeypatch):
    argv, image, root = real_argv(tmp_path, monkeypatch)
    assert host_access_violations(argv, image, root.parent) != []


def test_options_of_the_test_command_after_the_image_are_not_mistaken_for_docker_options(tmp_path, monkeypatch):
    """pytest -v is a test option. Only what comes before the image name can change the container."""
    command = ('python', '-m', 'pytest', '-v', '--ipc=host', '--privileged')
    argv, image, root = real_argv(tmp_path, monkeypatch, command)
    assert argv[-len(command):] == list(command)
    assert host_access_violations(argv, image, root) == []
