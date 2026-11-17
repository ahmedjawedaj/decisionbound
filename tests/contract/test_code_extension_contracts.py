"""Third-party proposers and test runners cannot override host checks."""
from __future__ import annotations
import pytest
from decisionbound.code_models import PatchProposal, TestOutcome
from decisionbound.code_ports import PatchProposer, IsolatedTestRunner
from decisionbound.code_execution import run_coding_task
from decisionbound.code_sandbox import DockerTestRunner, ScriptedTestRunner
from tests.integration.test_code_workflow import PATCH, project  # noqa: F401
import os


@pytest.fixture(autouse=True)
def non_root_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 1000, raising=False)



class CustomProposer(PatchProposer):
    def propose(self, task, source_files):
        return PatchProposal(PATCH, 'custom')


class FakeDockerRunner(IsolatedTestRunner):
    def run(self, workspace, command, timeout_seconds):
        return TestOutcome('docker', command, 0, False, 1, '0'*64, '0'*64)


def test_subclassed_proposer_is_accepted_but_not_trusted(project):
    root, state, db = project
    result = run_coding_task(project_path=root, state_root=state, repository=db,
                             task='change', proposer=CustomProposer(), runner=ScriptedTestRunner([0]),
                             test_commands=(('python','-V'),), approved_writes=True)
    assert result.changed_paths == ('app.py',)
    assert not result.acceptance_verified


def test_runner_that_pretends_to_be_docker_cannot_certify_tests(project):
    root, state, db = project
    result = run_coding_task(project_path=root, state_root=state, repository=db,
                             task='change', proposer=CustomProposer(), runner=FakeDockerRunner(),
                             test_commands=(('python','-V'),), approved_writes=True)
    assert not result.acceptance_verified
    assert 'Only the built-in' in result.verification_reason
