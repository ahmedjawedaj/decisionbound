"""The host must stay bounded and tidy however the container CLI behaves.

These tests run real child processes in place of the Docker CLI, so they check the actual reading,
killing and joining, not a fake.
"""
from __future__ import annotations

import _thread
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from decisionbound import code_sandbox
from decisionbound.code_sandbox import _run_cli

CAP = 100_000
POSIX = os.name == 'posix'


def child(code: str) -> list[str]:
    return [sys.executable, '-c', code]


def run(code: str, timeout: float = 20, cap: int = CAP):
    return _run_cli(child(code), dict(os.environ), timeout, cap)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def read_pid(path: Path, wait: float = 5) -> int:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        text = path.read_text() if path.exists() else ''
        if text.endswith('\n'):
            return int(text)
        time.sleep(0.02)
    raise AssertionError('child did not write its pid')


def test_small_output_is_hashed_exactly_and_nothing_is_flagged():
    result = run("import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)")
    assert result.returncode == 3
    assert result.stdout_sha256 == hashlib.sha256(b'out').hexdigest()
    assert result.stderr_sha256 == hashlib.sha256(b'err').hexdigest()
    assert result.total_bytes == 6 and result.drained
    assert not result.timed_out and not result.over_cap


def test_output_above_the_receipt_flag_but_below_the_cap_is_hashed_in_full():
    size = 90_000
    result = run(f"import sys; sys.stdout.buffer.write(b'a' * {size}); sys.stderr.buffer.write(b'b' * 5000)")
    assert result.stdout_sha256 == hashlib.sha256(b'a' * size).hexdigest()
    assert result.stderr_sha256 == hashlib.sha256(b'b' * 5000).hexdigest()
    assert result.total_bytes == size + 5000 and not result.over_cap


@pytest.mark.parametrize('extra,over', [(0, False), (1, True)], ids=['exactly-at-cap', 'one-byte-over'])
def test_the_cap_boundary(extra, over):
    result = run(f"import sys; sys.stdout.buffer.write(b'x' * {CAP + extra})")
    assert result.over_cap is over
    assert result.total_bytes == CAP + extra


def test_both_streams_share_one_cap():
    half = CAP // 2 + 10
    result = run(f"import sys; sys.stdout.buffer.write(b'x' * {half}); sys.stdout.flush(); "
                 f"sys.stderr.buffer.write(b'y' * {half})")
    assert result.over_cap


@pytest.mark.skipif(not POSIX, reason='checks the child by pid')
def test_unlimited_output_is_stopped_and_the_child_is_killed(tmp_path):
    pidfile = tmp_path / 'pid'
    code = (f"import os, sys; open({str(pidfile)!r}, 'w').write(str(os.getpid()) + '\\n')\n"
            "chunk = b'x' * 65536\n"
            "while True: sys.stdout.buffer.write(chunk)\n")
    started = time.monotonic()
    result = run(code, timeout=20)
    assert result.over_cap and not result.timed_out
    assert time.monotonic() - started < 15
    # Reading stops at the cap, so what was counted is bounded by the cap plus one read of each stream.
    assert CAP < result.total_bytes <= CAP + 2 * 65536
    assert not alive(read_pid(pidfile))


@pytest.mark.skipif(not POSIX, reason='checks the child by pid')
def test_a_run_that_outlasts_its_time_is_killed_and_keeps_the_output_so_far(tmp_path):
    pidfile = tmp_path / 'pid'
    code = (f"import os, sys, time; open({str(pidfile)!r}, 'w').write(str(os.getpid()) + '\\n')\n"
            "sys.stdout.write('x'); sys.stdout.flush(); time.sleep(60)\n")
    started = time.monotonic()
    result = run(code, timeout=1)
    assert result.timed_out and not result.over_cap and result.drained
    assert result.stdout_sha256 == hashlib.sha256(b'x').hexdigest()
    assert time.monotonic() - started < 15
    assert not alive(read_pid(pidfile))


@pytest.mark.skipif(not POSIX, reason='checks the child by pid')
def test_an_interrupt_kills_the_child_and_ends_the_readers(tmp_path):
    pidfile = tmp_path / 'pid'
    code = (f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid()) + '\\n'); time.sleep(60)\n")
    timer = threading.Timer(1.0, _thread.interrupt_main)
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run(code, timeout=60)
    finally:
        timer.cancel()
    assert not alive(read_pid(pidfile))
    assert not [t for t in threading.enumerate() if t.name == 'dbound-sandbox-drain' and t.is_alive()]


def test_no_reader_thread_is_left_after_a_normal_run():
    run("print('hello')")
    assert not [t for t in threading.enumerate() if t.name == 'dbound-sandbox-drain' and t.is_alive()]


@pytest.mark.skipif(not POSIX, reason='needs a process that keeps the pipe open after its parent exits')
def test_a_pipe_held_open_by_another_process_is_reported_and_its_stream_stays_open(tmp_path, monkeypatch):
    monkeypatch.setattr(code_sandbox, '_JOIN_SECONDS', 0.3)
    pidfile = tmp_path / 'pid'
    # The parent exits at once. Its child inherits the output pipes and keeps them open.
    code = ("import subprocess, sys\n"
            f"p = subprocess.Popen([sys.executable, '-c', \"import os, time; "
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()) + '\\\\n'); time.sleep(60)\"])\n")
    result = run(code, timeout=20)
    holder = read_pid(pidfile)
    try:
        assert result.returncode == 0
        assert not result.drained
        # A reader that is still blocked keeps a valid stream, so it can never read a reused descriptor.
        stuck = [t for t in threading.enumerate() if t.name == 'dbound-sandbox-drain' and t.is_alive()]
        assert stuck and all(not t.stream.closed for t in stuck)
    finally:
        os.kill(holder, 9)
        for t in threading.enumerate():
            if t.name == 'dbound-sandbox-drain':
                t.join(5)


def test_the_cli_is_started_without_a_shell_and_without_stdin(monkeypatch, tmp_path):
    import subprocess
    seen = {}
    real = subprocess.Popen

    def spy(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return real(argv, **kwargs)
    monkeypatch.setattr(code_sandbox.subprocess, 'Popen', spy)
    run("pass")
    assert not seen.get('shell') and seen['stdin'] == subprocess.DEVNULL
    assert isinstance(seen['argv'], list)


def test_the_receipt_waits_for_a_slow_reader_to_finish(monkeypatch):
    """Output the child wrote just before exiting must be in the hash even if the reader lags."""
    original = code_sandbox._Drain.run

    def slow(self):
        time.sleep(0.3)
        original(self)
    monkeypatch.setattr(code_sandbox._Drain, 'run', slow)
    result = run("import sys; sys.stdout.write('late'); sys.stderr.write('later')")
    assert result.stdout_sha256 == hashlib.sha256(b'late').hexdigest()
    assert result.stderr_sha256 == hashlib.sha256(b'later').hexdigest()
    assert result.total_bytes == 9 and result.drained
