"""Docker backend must never accidentally execute untrusted tests on host."""
from __future__ import annotations
import subprocess
import os
import re
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
# (issue 20). This check reads the argument list instead. It is an allowlist of exactly the options the
# runner emits, so any other option before the image name is a violation, whatever it does. It cannot see
# a mount that the daemon adds on its own, so it complements that probe and does not replace it.
# option -> (takes a value, how many times it appears). '-e' appears once for each environment value.
OPTION_SHAPE = {
    '--name': (True, 1), '--rm': (False, 1), '--network': (True, 1), '--pull': (True, 1),
    '--read-only': (False, 1), '--cap-drop': (True, 1), '--security-opt': (True, 1),
    '--pids-limit': (True, 1), '--memory': (True, 1), '--cpus': (True, 1), '--user': (True, 1),
    '--tmpfs': (True, 1), '-e': (True, 2), '--mount': (True, 1), '-w': (True, 1),
}
UID, GID = 1234, 5678


def expected_values(runner: DockerTestRunner, workspace_root: Path) -> dict:
    """The value of every option, taken from the runner's own settings where it has them.

    Only the container name varies from run to run (it carries a hash of the command), so it is the one
    value matched by pattern. Everything else is compared exactly, so a limit of 0 (no limit) or a gid of 0
    does not pass."""
    return {
        '--name': re.compile(r'dbound-[a-f0-9]{32}-[a-f0-9]{8}'),
        '--network': 'none', '--pull': 'never', '--cap-drop': 'ALL', '--security-opt': 'no-new-privileges',
        '--pids-limit': str(runner.pids), '--memory': f'{runner.memory_mb}m', '--cpus': str(runner.cpus),
        '--user': f'{UID}:{GID}', '--tmpfs': '/tmp:rw,nosuid,nodev,size=64m',
        '--mount': f'type=bind,src={workspace_root},dst=/workspace', '-w': '/workspace',
    }


ENVIRONMENT = ('PYTHONDONTWRITEBYTECODE=1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1')


def host_access_violations(argv: list[str], runner: DockerTestRunner, workspace_root: Path) -> list[str]:
    """Return every token before the image that the runner does not emit, or that is missing or repeated."""
    head = argv[:argv.index(runner.image)]
    problems: list[str] = []
    if head[:1] != [runner.binary] or head[1:2] != ['run']:
        return [f'the arguments start with {head[:2]}, not [{runner.binary!r}, \'run\']']
    wanted = expected_values(runner, workspace_root)
    seen: dict[str, list[str]] = {}
    i = 2
    while i < len(head):
        token = head[i]
        if token not in OPTION_SHAPE:
            problems.append(f'{token}: not an option the runner emits')
            i += 1
            continue
        if not OPTION_SHAPE[token][0]:
            seen.setdefault(token, []).append('')
            i += 1
            continue
        if i + 1 >= len(head):
            problems.append(f'{token}: no value')
            break
        value = head[i + 1]
        if token == '-e':
            ok = value in ENVIRONMENT
        elif token == '--name':
            ok = wanted[token].fullmatch(value) is not None
        else:
            ok = value == wanted[token]
        if not ok:
            problems.append(f'{token} {value}: value is not the one the runner emits')
        seen.setdefault(token, []).append(value)
        i += 2
    for option, (_, times) in OPTION_SHAPE.items():
        count = len(seen.get(option, []))
        if count != times:
            problems.append(f'{option}: appears {count} times, expected {times}')
    if len(set(seen.get('-e', []))) != len(seen.get('-e', [])):
        problems.append('-e: the same environment value is set twice')
    return problems


def real_argv(tmp_path, monkeypatch, command=('python', '-V')) -> tuple[list[str], DockerTestRunner, Path]:
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.setattr(os, 'getuid', lambda: UID, raising=False)
    monkeypatch.setattr(os, 'getgid', lambda: GID, raising=False)
    ws = workspace(tmp_path)
    image = 'python@sha256:' + 'a' * 64
    seen = []
    monkeypatch.setattr('decisionbound.code_sandbox.subprocess.run',
                        lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, b'', b''))
    runner = DockerTestRunner(image)
    runner.run(ws, command, 4)
    return seen[0], runner, Path(ws.path).resolve()


def test_the_real_argument_list_gives_the_container_only_the_workspace_and_tmp(tmp_path, monkeypatch):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    assert host_access_violations(argv, runner, root) == []


# Each control adds the given tokens just before the image name. The check must report a problem and
# must name the token that does the harm, so a pass cannot come from an unrelated reason.
EXTRA_OPTIONS = [
    (['-v', '/dev/shm:/hostshm'], '-v'),
    (['--volume=/dev/shm:/hostshm'], '--volume=/dev/shm:/hostshm'),
    (['-v/dev/shm:/hostshm'], '-v/dev/shm:/hostshm'),
    (['-itv', '/dev/shm:/hostshm'], '-itv'),
    (['-dv', '/dev/shm:/hostshm'], '-dv'),
    (['-tv', '/dev/shm:/hostshm'], '-tv'),
    (['--volumes-from', 'other'], '--volumes-from'),
    (['--mount', 'type=bind,src=/dev/shm,dst=/hostshm'], 'src=/dev/shm'),
    (['--mount=type=bind,src=/dev/shm,dst=/hostshm'], '--mount=type=bind,src=/dev/shm,dst=/hostshm'),
    (['--mount', 'type=tmpfs,dst=/dev/shm'], 'dst=/dev/shm'),
    (['--tmpfs', '/dev/shm'], '/dev/shm'),
    (['--tmpfs=/dev/shm'], '--tmpfs=/dev/shm'),
    (['--device', '/dev/mem'], '--device'),
    (['--device-cgroup-rule', 'a *:* rwm'], '--device-cgroup-rule'),
    (['--ipc', 'host'], '--ipc'),
    (['--ipc=host'], '--ipc=host'),
    (['--pid=host'], '--pid=host'),
    (['--uts', 'host'], '--uts'),
    (['--userns', 'host'], '--userns'),
    (['--cgroupns=host'], '--cgroupns=host'),
    (['--cgroup-parent', '/'], '--cgroup-parent'),
    (['--privileged'], '--privileged'),
    (['--cap-add', 'SYS_ADMIN'], '--cap-add'),
    (['--group-add', 'docker'], '--group-add'),
    (['--gpus', 'all'], '--gpus'),
    (['--runtime', 'other'], '--runtime'),
    (['--add-host', 'evil:10.0.0.1'], '--add-host'),
    (['--entrypoint', '/bin/sh'], '--entrypoint'),
    (['--link', 'other'], '--link'),
    (['--volume-driver', 'other'], '--volume-driver'),
    (['--storage-opt', 'size=1T'], '--storage-opt'),
    (['--sysctl', 'kernel.shmmax=1'], '--sysctl'),
    (['--shm-size', '1g'], '--shm-size'),
    (['--init'], '--init'),
    (['--hostname', 'x'], '--hostname'),
    (['--ulimit', 'nofile=1'], '--ulimit'),
    (['--oom-kill-disable'], '--oom-kill-disable'),
    (['--env-file', '/etc/environment'], '--env-file'),
    (['-e', 'HOME'], 'HOME'),
    (['-e', 'LD_PRELOAD=/workspace/x.so'], 'LD_PRELOAD'),
    (['--network=host'], '--network=host'),
    (['--network', 'host'], 'host'),
    (['--net', 'host'], '--net'),
    (['--security-opt', 'seccomp=unconfined'], 'seccomp=unconfined'),
    (['--security-opt=apparmor=unconfined'], '--security-opt=apparmor=unconfined'),
    (['--read-only=false'], '--read-only=false'),
    (['stray-positional'], 'stray-positional'),
]


@pytest.mark.parametrize('extra,culprit', EXTRA_OPTIONS, ids=lambda e: ' '.join(e) if isinstance(e, list) else None)
def test_control_an_extra_option_is_flagged(tmp_path, monkeypatch, extra, culprit):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    at = argv.index(runner.image)
    found = host_access_violations(argv[:at] + extra + argv[at:], runner, root)
    assert any(culprit in problem for problem in found), found


@pytest.mark.parametrize('prefix', [['-H', 'tcp://evil:2375'], ['--host', 'tcp://evil:2375'],
                                    ['--context', 'remote'], ['-c', 'remote']])
def test_control_a_daemon_selecting_option_before_run_is_flagged(tmp_path, monkeypatch, prefix):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    found = host_access_violations([argv[0], *prefix, *argv[1:]], runner, root)
    assert any(prefix[0] in problem for problem in found), found


@pytest.mark.parametrize('option', sorted(OPTION_SHAPE))
def test_control_removing_any_required_option_is_flagged(tmp_path, monkeypatch, option):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    takes_value = OPTION_SHAPE[option][0]
    kept, skip = [], 0
    for token in argv[:argv.index(runner.image)]:
        if skip:
            skip -= 1
        elif token == option:
            skip = 1 if takes_value else 0
        else:
            kept.append(token)
    found = host_access_violations(kept + argv[argv.index(runner.image):], runner, root)
    assert any(option in problem for problem in found), found


def test_control_replacing_the_tmp_tmpfs_with_another_path_is_flagged(tmp_path, monkeypatch):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    at = argv.index('--tmpfs')
    argv[at + 1] = '/dev/shm:rw,nosuid,nodev,size=64m'
    found = host_access_violations(argv, runner, root)
    assert any('/dev/shm' in problem for problem in found), found


def test_control_a_workspace_mount_of_another_directory_is_flagged(tmp_path, monkeypatch):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    found = host_access_violations(argv, runner, root.parent)
    assert any('--mount' in problem for problem in found), found


def test_control_a_value_that_runs_into_the_image_name_is_flagged(tmp_path, monkeypatch):
    """A dangling option must not swallow the image name and hide the problem."""
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    at = argv.index(runner.image)
    found = host_access_violations(argv[:at] + ['--ipc'] + argv[at:], runner, root)
    assert any('--ipc' in problem for problem in found), found


@pytest.mark.parametrize('option,bad', [
    ('--user', '0:0'), ('--user', '00:0'), ('--user', '000:5678'), ('--user', '1234:0'), ('--user', '0001:0'),
    ('--user', '\u0661\u0662\u0663\u0664:5678'),
    ('--pids-limit', '0'), ('--pids-limit', '-1'), ('--pids-limit', '99999'),
    ('--memory', '0m'), ('--memory', '99999m'), ('--memory', '-1'),
    ('--cpus', '0'), ('--cpus', '99'),
    ('--tmpfs', '/tmp:rw,nosuid,nodev,size=0m'), ('--tmpfs', '/tmp:rw,nosuid,nodev,exec,size=64m'),
    ('--name', 'evil'), ('--name', 'dbound-1'),
])
def test_control_a_wrong_value_for_an_option_the_runner_emits_is_flagged(tmp_path, monkeypatch, option, bad):
    """Limits of 0 mean no limit and a group of 0 is root, so values are compared exactly, not by pattern."""
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    argv[argv.index(option) + 1] = bad
    found = host_access_violations(argv, runner, root)
    assert any(option in problem and bad in problem for problem in found), found


def test_control_a_different_binary_or_a_token_before_run_is_flagged(tmp_path, monkeypatch):
    argv, runner, root = real_argv(tmp_path, monkeypatch)
    assert host_access_violations(['evil', *argv[1:]], runner, root) != []


def test_options_of_the_test_command_after_the_image_are_not_mistaken_for_docker_options(tmp_path, monkeypatch):
    """pytest -v is a test option. Only what comes before the image name can change the container."""
    command = ('python', '-m', 'pytest', '-v', '--ipc=host', '--privileged')
    argv, runner, root = real_argv(tmp_path, monkeypatch, command)
    assert argv[-len(command):] == list(command)
    assert host_access_violations(argv, runner, root) == []
