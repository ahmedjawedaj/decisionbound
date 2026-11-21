"""Pluggable test executors with explicit trust labels and isolation boundaries.

The default test executor is an inert deterministic mock for CI and research.
Real execution requires an explicitly authorized Docker-compatible daemon:
no network, no privileges, explicit CPU/memory/PID and timeout ceilings.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from .code_models import TestOutcome, Workspace
from .code_ports import IsolatedTestRunner


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# A test that prints without limit must not fill the host. The Docker CLI's output is hashed as it
# arrives and never stored. Past OUTPUT_FLAG_BYTES the receipt is flagged as truncated, which the
# workflow treats as a failed test. Past OUTPUT_HARD_CAP_BYTES (both streams together) the run is
# stopped and its container removed.
OUTPUT_FLAG_BYTES = 128_000
OUTPUT_HARD_CAP_BYTES = 4 * 1024 * 1024
_READ_CHUNK = 65536
_JOIN_SECONDS = 5.0


@dataclass(frozen=True)
class _CliResult:
    """What one bounded run of the container CLI produced. Output is represented by hashes only."""
    returncode: int | None
    stdout_sha256: str
    stderr_sha256: str
    total_bytes: int
    timed_out: bool = False
    over_cap: bool = False
    drained: bool = True


class _Budget:
    """Byte count shared by the two output readers."""

    def __init__(self, cap: int):
        self.cap, self.total = cap, 0
        self.over = threading.Event()
        self._lock = threading.Lock()

    def take(self, count: int) -> bool:
        with self._lock:
            self.total += count
            if self.total > self.cap:
                self.over.set()
                return False
            return True


class _Drain(threading.Thread):
    """Reads one pipe to its end, hashing the bytes and keeping none of them."""

    def __init__(self, stream, budget: _Budget):
        super().__init__(name='dbound-sandbox-drain', daemon=True)
        self.stream, self.budget = stream, budget
        self.digest = hashlib.sha256()

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read1(_READ_CHUNK)
                if not chunk or not self.budget.take(len(chunk)):
                    return
                self.digest.update(chunk)
        except (OSError, ValueError):
            return


def _run_cli(argv: list[str], env: dict[str, str], timeout: float,
             cap: int = OUTPUT_HARD_CAP_BYTES) -> _CliResult:
    """Run the container CLI with bounded memory. The CLI is always dead and its readers finished
    (or reported unfinished) before this returns or raises."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    budget = _Budget(cap)
    drains = [_Drain(proc.stdout, budget), _Drain(proc.stderr, budget)]
    timed_out = False
    try:
        for drain in drains:
            drain.start()
        deadline = time.monotonic() + timeout
        while True:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if budget.over.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        proc.wait()
        for drain in drains:
            if drain.is_alive():
                drain.join(_JOIN_SECONDS)
        # A reader still blocked (something else holds the pipe) keeps its stream, so a closed and
        # reused file descriptor can never be read by it.
        for drain in drains:
            if not drain.is_alive():
                drain.stream.close()
    drained = not any(d.is_alive() for d in drains)
    return _CliResult(proc.returncode, drains[0].digest.hexdigest(), drains[1].digest.hexdigest(),
                      budget.total, timed_out, budget.over.is_set(), drained)


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
        is hashed as it arrives rather than stored or copied into durable
        execution logs. A run that prints more than OUTPUT_HARD_CAP_BYTES is
        stopped and its container removed. Users can inspect test errors
        separately through a controlled local workflow.
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
        # --memory-swap equal to --memory leaves no swap. --log-driver none stops the daemon from also
        # writing everything the test prints to its own log on the host disk.
        argv = [self.binary, 'run', '--name', name, '--rm', '--network', 'none', '--pull', 'never',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--log-driver', 'none',
                '--pids-limit', str(self.pids), '--memory', f'{self.memory_mb}m',
                '--memory-swap', f'{self.memory_mb}m',
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
            cli = _run_cli(argv, env, timeout_seconds + 5)
        except BaseException as exc:
            # Python stops waiting for the CLI here (interrupt, memory error, OS error). Killing the CLI
            # does not stop the container, so remove it by name before the error goes on. A failure of
            # this cleanup must not hide the original error, so it is only noted on it. This does not
            # help when the Python process itself is killed (SIGTERM, SIGKILL, power loss).
            try:
                cleanup = subprocess.run([self.binary, 'rm', '-f', name],
                                         capture_output=True, timeout=15, env=env)
                if cleanup.returncode != 0 and b'No such container' not in (cleanup.stderr or b''):
                    exc.add_note(f'Sandbox container {name} may still be running: cleanup failed')
            except (OSError, subprocess.TimeoutExpired):
                exc.add_note(f'Sandbox container {name} may still be running: cleanup unconfirmed')
            raise
        if cli.timed_out or cli.over_cap:
            # The CLI was killed, which does not stop the container. Remove the exact run-scoped
            # container before returning a receipt.
            try:
                kill = subprocess.run([self.binary, 'rm', '-f', name],
                                      capture_output=True, timeout=15, env=env)
            except (OSError, subprocess.TimeoutExpired) as err:
                raise RuntimeError('Sandbox stopped and container cleanup is unconfirmed') from err
            if kill.returncode != 0 and b'No such container' not in kill.stderr:
                raise RuntimeError('Sandbox stopped and container cleanup failed')
            duration = int((time.monotonic() - started) * 1000)
            return TestOutcome('docker', command, None, cli.timed_out, duration, cli.stdout_sha256,
                               cli.stderr_sha256, cli.over_cap or cli.total_bytes > 0)
        duration = int((time.monotonic() - started) * 1000)
        return TestOutcome('docker', command, cli.returncode, False, duration,
                           cli.stdout_sha256, cli.stderr_sha256,
                           cli.total_bytes > OUTPUT_FLAG_BYTES or not cli.drained)
