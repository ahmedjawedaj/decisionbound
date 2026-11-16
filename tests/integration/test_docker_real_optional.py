"""Real network-off Docker smoke test, enabled on connected CI runners.

Run with DBOUND_DOCKER_TEST_IMAGE=python:3.12-slim (without hidden character)
after pre-pulling the image. Skips explicitly when Docker isn't available.
"""
from __future__ import annotations
import os
import shutil
import pytest
from decisionbound.code_sandbox import DockerTestRunner
from tests.integration.test_code_workflow import PATCH, project, execute  # noqa: F401

IMAGE = os.environ.get('DBOUND_DOCKER_TEST_IMAGE')


@pytest.mark.skipif(not IMAGE or not shutil.which('docker'), reason='Docker smoke requires a pre-pulled image and local daemon')
def test_real_python_exec_in_network_disabled_container(project):
    runner = DockerTestRunner(IMAGE)
    result = execute(project, runner=runner,
                     test_commands=(('python', '-c', 'import app; assert app.VALUE == 2'),))
    assert result.acceptance_verified, result.verification_reason
    assert result.task_completed is False  # semantic task approval still required
    assert result.tests[0].kind == 'docker'
    assert result.tests[0].exit_code == 0
    assert (project[0]/'app.py').read_text() == 'VALUE = 1\n'
