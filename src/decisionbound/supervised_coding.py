"""One-call supervised coding workflow with explicit authorization and review.

The pipeline is `prepare -> implement -> audit`. Each stage is a separate
journalled task. The implementer produces isolated, reviewable artifacts; the
auditor reconstructs host receipts after real tests pass. Even successful
verification means only the operator's acceptance *contract* was met, not
unbounded semantic completion or permission to merge into a repository.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .code_acceptance import TaskContract
from .code_ports import IsolatedTestRunner
from .code_repair import RepairLimits, RepairProposerFactory
from .code_review import CodeReviewPolicy
from .code_workspace import GitWorktreeManager
from .code_patches import PatchPolicy
from .identity import load_project
from .storage import SQLiteRepository
from .workflows import (
    CodeEvidenceReviewWorker, CodingRepairWorker, IndependentTaskVerifier,
    PreparationWorker, WorkflowJournal, WorkflowSpec, WorkflowSupervisor,
    WorkflowTask,
)


@dataclass(frozen=True)
class SupervisedCodingRequest:
    """Explicitly approved bounds for a single supervised coding assignment.

    The contract is authored by a human/operator, not the coding model. The
    proposer is never allowed to write to the repository: only DecisionBound
    may apply a validated patch in a disposable Git worktree. Provider API
    keys and private file contents must not appear in this request.
    """

    task: str
    contract: TaskContract
    source_targets: tuple[str, ...] = ()
    limits: RepairLimits = RepairLimits(max_attempts=2)
    max_context_tokens: int = 1200
    require_red_test: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not 1 <= len(self.task) <= 3000:
            raise ValueError('Coding task description must be 1–3000 characters')
        if not isinstance(self.contract, TaskContract) or not isinstance(self.limits, RepairLimits):
            raise TypeError('Task contract and repair limits must use published types')
        if (not isinstance(self.source_targets, tuple) or len(self.source_targets) > 8 or
                not set(self.source_targets) <= set(self.contract.allowed_paths) or
                not 100 <= self.max_context_tokens <= 20000 or
                type(self.require_red_test) is not bool):
            raise ValueError('Invalid source-file/context bounds')

    def workflow(self, *, approval_sha256: str | None = None) -> WorkflowSpec:
        """Construct an immutable, three-stage execution and review DAG.

        This does not enable tools or perform writes. It binds the budget and
        prevents a reviewer from running before real code acceptance is checked.
        """
        return WorkflowSpec((
            WorkflowTask('plan', 'prepare', self.task, role='planner',
                         max_context_tokens=self.max_context_tokens),
            WorkflowTask('implement', 'code', self.task, depends_on=('plan',),
                         max_cost_usd=self.limits.max_model_cost_usd,
                         max_context_tokens=self.max_context_tokens, role='implementer'),
            WorkflowTask('audit', 'review', 'Reconstruct host-side coding evidence',
                         depends_on=('implement',), role='reviewer'),
        ), max_workers=2, max_total_cost_usd=self.limits.max_model_cost_usd,
           approval_sha256=approval_sha256)


class SupervisedCodingOrchestrator:
    """Operator-owned entrypoint for one sandboxed coding DAG.

    This class is deliberately small: it wires already tested host components
    and does not invent a second journal, patch validator, or verifier. Its
    dependency injection enables offline scripted tests and a real Docker
    backend in connected environments.

    Args:
        repository: Local protected SQLite store.
        state_root: Private storage for isolated worktrees and artifacts.
        runner: Operator-configured isolated test runner.
        proposer_factory: Operator-configured provider or scripted proposer.
        policy: Optional veto-only review policy.
    """

    def __init__(self, *, repository: SQLiteRepository, state_root: Path,
                 runner: IsolatedTestRunner, proposer_factory: RepairProposerFactory,
                 review_policy: CodeReviewPolicy | None = None):
        if not isinstance(repository, SQLiteRepository):
            raise TypeError('Supervised coding currently requires the local SQLite adapter')
        if not isinstance(runner, IsolatedTestRunner) or not isinstance(proposer_factory, RepairProposerFactory):
            raise TypeError('Runner/proposer must implement published extension ports')
        if review_policy is not None and not isinstance(review_policy, CodeReviewPolicy):
            raise TypeError('Review policy must implement a veto-only extension')
        self.repository, self.state_root = repository, state_root
        self.runner, self.proposer_factory = runner, proposer_factory
        self.review_policy = review_policy

    def execute(self, *, project_path: Path, request: SupervisedCodingRequest,
                idempotency_key: str, approved_writes: bool = False,
                lease_seconds: int = 180, max_wall_seconds: int = 600) -> dict:
        """Run one authorized workflow and return a source-free audit summary.

        Never commits, merges or pushes. No job runs without explicit write
        authorization. Restarting with the same key returns the prior workflow
        without replaying completed or ambiguously interrupted coding work.
        """
        if approved_writes is not True:
            raise PermissionError('A supervised coding workflow needs explicit write approval')
        if not isinstance(request, SupervisedCodingRequest):
            raise TypeError('Request must use the versioned supervised coding contract')
        project = load_project(project_path, self.repository)
        root, _ = GitWorktreeManager.check_source(project_path)
        request.contract.preflight(root, PatchPolicy(), request.contract.test_commands)
        # Bind the immutable workflow to approved configuration, runner trust
        # class, and scripted patch bytes. Reusing a key after changing from
        # MOCK to DOCKER, contract, source scope or patch now conflicts instead
        # of silently returning a previous incompatible result.
        from .code_repair import ScriptedRepairFactory
        adapter = type(self.runner)
        proposer_type = type(self.proposer_factory)
        binding = {
            'contract_sha256': request.contract.sha256,
            'task_sha256': hashlib.sha256(request.task.encode()).hexdigest(),
            'source_targets': request.source_targets,
            'max_attempts': request.limits.max_attempts,
            'max_model_cost_usd': request.limits.max_model_cost_usd,
            'require_red_test': request.require_red_test,
            'runner_type': f'{adapter.__module__}.{adapter.__qualname__}',
            'runner_image': getattr(self.runner, 'image', None),
            'proposer_type': f'{proposer_type.__module__}.{proposer_type.__qualname__}',
            'patch_hashes': ([hashlib.sha256(p.encode()).hexdigest()
                             for p in self.proposer_factory.patches]
                             if isinstance(self.proposer_factory, ScriptedRepairFactory) else None),
        }
        fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        journal = WorkflowJournal(self.repository)
        wid = journal.create(project_path=root,
                             spec=request.workflow(approval_sha256=fingerprint),
                             idempotency_key=idempotency_key)
        workers = {
            'prepare': PreparationWorker(self.repository),
            'code': CodingRepairWorker(repository=self.repository, state_root=self.state_root,
                approved={'implement': {
                    'approved_writes': True,
                    'contract': request.contract,
                    'factory': self.proposer_factory,
                    'runner': self.runner,
                    'limits': request.limits,
                    'source_targets': request.source_targets,
                    'require_red_test': request.require_red_test,
                }}),
            'review': CodeEvidenceReviewWorker(repository=self.repository,
                                               state_root=self.state_root, policy=self.review_policy),
        }
        supervisor = WorkflowSupervisor(journal, workers=workers,
                                       verifier=IndependentTaskVerifier(self.repository, self.state_root))
        result = supervisor.run(wid, lease_seconds=lease_seconds, max_wall_seconds=max_wall_seconds)
        return {**result, 'project_id': project.project_id,
                'patch_auto_merged': False, 'task_completed': False}
