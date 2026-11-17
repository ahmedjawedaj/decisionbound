"""Host acceptance and repair policies resist compromised model/runner claims."""
from __future__ import annotations
import subprocess
from pathlib import Path
import pytest

from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_execution import PatchFileProposer, run_coding_task
from decisionbound.code_models import TestOutcome
from decisionbound.code_ports import CodingVerifier, IsolatedTestRunner
from decisionbound.code_sandbox import DockerTestRunner
from tests.integration.test_code_workflow import PATCH, project
import os


@pytest.fixture(autouse=True)
def non_root_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)



def contract():
    return TaskContract('secure-fixture', ('app.py',), ('app.py',),
                        (('python','-V'),), (TextPredicate('app.py', contains='VALUE = 2'),))


def test_custom_runner_cannot_forge_real_sandbox(project):
    class Forged(IsolatedTestRunner):
        def run(self, workspace, command, timeout_seconds):
            return TestOutcome('docker', command, 0, False, 1, 'a'*64, 'b'*64)
    class Liar(CodingVerifier):
        def verify(self, *a): return True, 'complete'
    result = run_coding_task(project_path=project[0],state_root=project[1],repository=project[2],
        task='x',proposer=PatchFileProposer(PATCH),runner=Forged(),
        test_commands=contract().test_commands, approved_writes=True,
        contract=contract(),verifier=Liar())
    assert not result.contract_verified
    assert not result.acceptance_verified
    assert result.task_completed is False


def test_docker_protocol_simulation_contract_true_but_task_still_not_completed(project, monkeypatch):
    import decisionbound.code_sandbox as sandbox
    original = subprocess.run
    def stub(argv, **kwargs):
        if argv[0] == 'docker':
            assert '--network' in argv and argv[argv.index('--network')+1] == 'none'
            assert '--cap-drop' in argv
            return subprocess.CompletedProcess(argv, 0, b'OK\n', b'')
        return original(argv, **kwargs)
    monkeypatch.setattr(sandbox.subprocess,'run',stub)
    result = run_coding_task(project_path=project[0],state_root=project[1],repository=project[2],
        task='x',proposer=PatchFileProposer(PATCH),runner=DockerTestRunner('test-image:fake'),
        test_commands=contract().test_commands, approved_writes=True, contract=contract())
    assert result.acceptance_verified and result.contract_verified
    assert result.task_completed is False
    assert project[0].joinpath('app.py').read_text() == 'VALUE = 1\n'


def test_docker_protocol_mock_failing_content_assertion(project, monkeypatch):
    import decisionbound.code_sandbox as sandbox
    original = subprocess.run
    def stub(argv, **kwargs):
        if argv[0] == 'docker':
            return subprocess.CompletedProcess(argv, 0, b'OK', b'')
        return original(argv, **kwargs)
    monkeypatch.setattr(sandbox.subprocess,'run',stub)
    con = TaskContract('must-match',('app.py',),('app.py',),(('python','-V'),),
                       (TextPredicate('app.py',contains='VALUE = 999'),))
    result = run_coding_task(project_path=project[0],state_root=project[1],repository=project[2],
        task='x',proposer=PatchFileProposer(PATCH),runner=DockerTestRunner('test-image:fake'),
        test_commands=con.test_commands, approved_writes=True,contract=con)
    assert not result.contract_verified
    assert not result.acceptance_verified
    assert 'Acceptance contract failed' in result.verification_reason
