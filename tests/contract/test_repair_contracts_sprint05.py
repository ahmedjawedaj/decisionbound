"""Public override contracts and strict task-acceptance shape tests."""
from __future__ import annotations
import pytest
from pathlib import Path
from decisionbound.code_acceptance import TaskContract, TextPredicate
from decisionbound.code_repair import RepairProposerFactory, RepairFeedback, run_repair_session, RepairLimits
from decisionbound.code_execution import PatchFileProposer
from decisionbound.code_sandbox import ScriptedTestRunner
from tests.integration.test_code_workflow import PATCH, project


def test_subclass_factory_and_task_contract_json_roundtrip(project):
    class MyFactory(RepairProposerFactory):
        def __init__(self): self.seen=[]
        @property
        def requires_budget_reservation(self) -> bool:
            """This fixture produces no paid model calls."""
            return False
        def make(self, attempt, feedback, remaining_cost_usd):
            self.seen.append((attempt, feedback.failure_kind))
            return PatchFileProposer(PATCH)
    root, state, db = project
    contract = TaskContract('fixture-contract', ('app.py',), ('app.py',),
             (('python', '-m', 'pytest', '-q'),), (TextPredicate('app.py', contains='VALUE = 2'),))
    assert TaskContract.from_dict(contract.to_dict()).sha256 == contract.sha256
    f = MyFactory()
    result = run_repair_session(idempotency_key='custom_factory_example', project_path=root,
                state_root=state, repository=db, task='update', contract=contract,
                proposer_factory=f, runner=ScriptedTestRunner([1,0]),
                limits=RepairLimits(max_attempts=2), source_targets=('app.py',), approved_writes=True)
    assert result.status == 'needs_real_tests'
    assert f.seen == [(1,''), (2,'failed_test')]


def test_acceptance_path_security_is_host_owned(project):
    from decisionbound.code_patches import PatchPolicy, UnsafePatch
    contract = TaskContract('fixture-contract', ('app.py', '.env'), ('app.py',),
            (('python', '-m', 'pytest'),), (TextPredicate('app.py', contains='x'),))
    with pytest.raises(UnsafePatch, match='Protected'):
        contract.preflight(project[0], PatchPolicy(), contract.test_commands)


def test_mock_evidence_never_becomes_semantic_completion(project):
    contract = TaskContract('fixture-contract', ('app.py',), ('app.py',),
            (('python', '-m', 'pytest'),), (TextPredicate('app.py', contains='VALUE = 2'),))
    from decisionbound.code_execution import run_coding_task
    outcome = run_coding_task(project_path=project[0], state_root=project[1], repository=project[2],
        task='update', proposer=PatchFileProposer(PATCH), runner=ScriptedTestRunner([0]),
        test_commands=contract.test_commands, approved_writes=True, contract=contract)
    assert outcome.contract_sha256 == contract.sha256
    assert not outcome.contract_verified
    assert not outcome.task_completed
