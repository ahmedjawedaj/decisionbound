"""Pluggable test executors with explicit trust labels and isolation boundaries.

The default test executor is an inert deterministic mock for CI and research.
Real execution requires an explicitly authorized Docker-compatible daemon:
no network, no privileges, explicit CPU/memory/PID and timeout ceilings.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
import re
from collections.abc import Callable
from pathlib import Path
from .code_models import TestOutcome, Workspace
from .code_ports import IsolatedTestRunner


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_command(command: tuple[str, ...], timeout_seconds: int) -> None:
    if not command or len(command) > 16 or not 1 <= timeout_seconds <= 600:
        raise ValueError('Invalid test command or timeout')
    for part in command:
        if not isinstance(part, str) or not part or len(part) > 1024 or '\x00' in part:
            raise ValueError('Invalid test command argument')


class ScriptedTestRunner(IsolatedTestRunner):
    """Deterministic test fake; does not execute or certify user code.

    Args:
        checker: Optional function to inspect the disposable workspace and
            return a fake exit code. It is test infrastructure, not sandboxing.
    """

    def __init__(self, exit_codes: list[int] | None = None,
                 checker: Callable[[Workspace, tuple[str, ...]], int] | None = None):
        self.exit_codes = list(exit_codes or [0])
        self.checker = checker
        self.calls: list[tuple[Workspace, tuple[str, ...]]] = []

    def run(self, workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome:
        """Return a clearly labeled mock receipt for deterministic tests."""
        _validate_command(command, timeout_seconds)
        self.calls.append((workspace, command))
        if self.checker is not None:
            code = self.checker(workspace, command)
        elif self.exit_codes:
            code = self.exit_codes.pop(0)
        else:
            raise RuntimeError('No scripted outcome available')
        if type(code) is not int:
            raise ValueError('Scripted exit code must be an integer')
        return TestOutcome('mock', command, code, False, 0, _hash(b''), _hash(b''))


class DockerTestRunner(IsolatedTestRunner):
    """Run untrusted project tests in a constrained, network-disabled container.

    This backend assumes the operator trusts the local Docker daemon and its
    image provenance. Docker itself must be installed/configured by the user;
    container escape resistance is not independently certified by this code.

    By design the container has no mounted home, secrets, SSH agent or Docker
    socket; only the isolated worktree is mounted read/write. It may mutate
    that worktree, which the verifier detects after execution.
    A root caller is refused before contacting the daemon. On platforms without
    POSIX user IDs the container uses the fixed unprivileged ID 1000:1000.
    """

    def __init__(self, image: str, *, docker_binary: str = 'docker',
                 memory_mb: int = 512, cpus: float = 1.0, pids: int = 64):
        if (not image or any(c.isspace() for c in image) or image.startswith('-') or
                not 128 <= memory_mb <= 4096 or not 0.1 <= cpus <= 4.0 or
                not 8 <= pids <= 256 or docker_binary not in ('docker', 'podman')):
            raise ValueError('Invalid sandbox configuration')
        self.image, self.binary = image, docker_binary
        self.memory_mb, self.cpus, self.pids = memory_mb, cpus, pids

    def run(self, workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome:
        """Run a command with kernel-enforced limits; never use host shell.

        The caller must explicitly opt in to real container execution. Output
        is hashed rather than copied into durable execution logs; users can
        inspect test errors separately through a controlled local workflow.
        """
        _validate_command(command, timeout_seconds)
        uid = getattr(os, 'getuid', lambda: 1000)()
        gid = getattr(os, 'getgid', lambda: 1000)()
        if uid == 0:
            raise PermissionError('DockerTestRunner refuses a root caller; run as a non-root user')
        original = Path(workspace.path)
        if original.is_symlink():
            raise ValueError('Symlinked workspace')
        root = original.resolve(strict=True)
        # A Windows drive prefix such as ``C:`` is safe; any other colon is not.
        mount_path = str(root)[len(root.drive):]
        if not root.is_dir() or any(c in mount_path for c in ',:\n\r'):
            raise ValueError('Workspace path cannot be mounted safely')
        daemon = os.environ.get('DOCKER_HOST', '')
        if daemon and not daemon.startswith('unix://'):
            raise PermissionError('Remote Docker daemons are not authorized')
        if not re.fullmatch(r'code_[a-f0-9]{32}', workspace.run_id):
            raise ValueError('Invalid workspace run identifier')
        name = 'dbound-' + workspace.run_id[5:] + '-' + _hash('\0'.join(command).encode())[:8]
        argv = [self.binary, 'run', '--name', name, '--rm', '--network', 'none', '--pull', 'never',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', str(self.pids), '--memory', f'{self.memory_mb}m',
                '--cpus', str(self.cpus), '--user', f'{uid}:{gid}',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
                '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                '--mount', f'type=bind,src={root},dst=/workspace', '-w', '/workspace',
                self.image, *command]
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'HOME': str(Path.home())}
        if daemon:
            env['DOCKER_HOST'] = daemon
        started = time.monotonic()
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout_seconds + 5,
                                    env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            # The daemon may have left a container running after CLI timeout.
            # Kill the exact run-scoped container before returning a receipt.
            try:
                kill = subprocess.run([self.binary, 'rm', '-f', name],
                                      capture_output=True, timeout=15, env=env)
            except (OSError, subprocess.TimeoutExpired) as err:
                raise RuntimeError('Sandbox timed out and container cleanup is unconfirmed') from err
            if kill.returncode != 0 and b'No such container' not in kill.stderr:
                raise RuntimeError('Sandbox timed out and container cleanup failed')
            duration = int((time.monotonic() - started) * 1000)
            return TestOutcome('docker', command, None, True, duration, _hash(exc.stdout or b''),
                               _hash(exc.stderr or b''), bool((exc.stdout or b'') or (exc.stderr or b'')))
        duration = int((time.monotonic() - started) * 1000)
        return TestOutcome('docker', command, result.returncode, False, duration,
                           _hash(result.stdout), _hash(result.stderr),
                           len(result.stdout) + len(result.stderr) > 128_000)
