"""Docker backend must never accidentally execute untrusted tests on host."""
from __future__ import annotations
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest
from decisionbound.code_models import Workspace
from decisionbound.code_sandbox import DockerTestRunner, ScriptedTestRunner


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
