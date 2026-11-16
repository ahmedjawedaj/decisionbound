"""Connected-CI gate: real sandbox + fully verified prepare/code/audit DAG.

Not a mock. Requires a local Docker daemon and pre-pulled reviewed image.
"""
from __future__ import annotations

import os
import shutil
import pytest

from decisionbound.code_repair import ScriptedRepairFactory
from decisionbound.code_sandbox import DockerTestRunner
from decisionbound.supervised_coding import SupervisedCodingOrchestrator
from tests.integration.test_supervised_sprint07 import PATCH, repo, request  # noqa: F401


@pytest.mark.skipif(not os.environ.get('DBOUND_DOCKER_TEST_IMAGE') or not shutil.which('docker'),
                    reason='Real Docker and an explicit image are required')
def test_connected_docker_can_pass_actual_review_after_real_tests(repo):
    """Require every stage verified; never automatically merge or claim semantic completion."""
    store, home, root, contract = repo
    runner = DockerTestRunner(os.environ['DBOUND_DOCKER_TEST_IMAGE'])
    result = SupervisedCodingOrchestrator(repository=store, state_root=home,
        proposer_factory=ScriptedRepairFactory((PATCH,)), runner=runner).execute(
        project_path=root, request=__import__('decisionbound.supervised_coding',
           fromlist=['SupervisedCodingRequest']).SupervisedCodingRequest(
             'Fix known failing regression', contract, ('app.py',),
             require_red_test=True),
        idempotency_key='connected_docker_sprint07', approved_writes=True)
    assert result['state'] == 'verified_stages', result
    assert all(t['state']=='verified' for t in result['tasks'])
    assert result['task_completed'] is False
    from decisionbound.workflows import WorkflowJournal
    assert WorkflowJournal(store).attest(result['workflow_id'], state_root=home)['review_attestation'] == 'current'
    assert (root/'app.py').read_text() == 'VALUE = 1\n'
