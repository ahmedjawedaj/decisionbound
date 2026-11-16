"""Bounded, host-controlled iterative repair with crash-reconstructable state.

Unlike autonomous coding agents, this coordinator does not execute generated
shell commands, reuse patches after ambiguous writes, or treat the agent's
completion claim as evidence. Each attempt uses a fresh isolated Git worktree
and independently recorded test/contract results. Only explicit operator-
authorized writes are allowed, and all attempts remain reviewable patches.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .code_acceptance import TaskContract
from .code_execution import PatchFileProposer, run_coding_task
from .code_journal import RepairConflict, RepairState, SQLiteRepairJournal
from .code_models import CodingOutcome
from .code_ports import IsolatedTestRunner, PatchProposer
from .code_patches import PatchPolicy
from .code_workspace import GitWorktreeManager
from .identity import load_project
from .storage import SQLiteRepository


@dataclass(frozen=True)
class RepairLimits:
    """Hard attempt/cost ceilings and a best-effort wall-clock limit.

    Individual provider and sandbox calls also need their own timeouts; this
    wall-clock limit is checked *between* attempts and does not kill a provider.
    """

    max_attempts: int = 2
    max_wall_seconds: int = 600
    max_model_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if (not 1 <= self.max_attempts <= 5 or not 10 <= self.max_wall_seconds <= 3600 or
                not math.isfinite(self.max_model_cost_usd) or not 0 <= self.max_model_cost_usd <= 100):
            raise ValueError('Invalid bounded repair limits')


@dataclass(frozen=True)
class RepairFeedback:
    """Source-free, bounded feedback from independently recorded checks.

    Failed test argv, stdout, stderr, raw source, and credentials never enter
    this object. A provider may use the short failure category to propose a
    *new* patch against the original source snapshot.
    """

    failure_kind: str = ''
    failed_test_indices: tuple[int, ...] = ()
    previous_attempt_sha256: str = ''

    def to_dict(self) -> dict:
        """Serialize only the minimal diagnostics needed for the next attempt."""
        return {'failure_kind': self.failure_kind,
                'failed_test_indices': list(self.failed_test_indices),
                'previous_attempt_sha256': self.previous_attempt_sha256}

    @classmethod
    def from_dict(cls, value: dict) -> 'RepairFeedback':
        """Reject unrecognized journal feedback before passing it to a model."""
        if not value:
            return cls()
        if set(value) != {'failure_kind', 'failed_test_indices', 'previous_attempt_sha256'}:
            raise ValueError('Invalid stored feedback fields')
        if (value['failure_kind'] not in {'failed_test', 'failed_contract'} or
                not isinstance(value['failed_test_indices'], list) or
                any(type(x) is not int or not 0 <= x < 5 for x in value['failed_test_indices']) or
                not isinstance(value['previous_attempt_sha256'], str) or
                len(value['previous_attempt_sha256']) > 64):
            raise ValueError('Invalid stored feedback')
        return cls(value['failure_kind'], tuple(value['failed_test_indices']),
                   value['previous_attempt_sha256'])


class RepairProposerFactory(ABC):
    """Override to select model/provider or synthesize a patch per attempt.

    ``make`` must not perform any file changes. Network-capable factories must
    enforce request-level budgets before sending paid calls and account for
    ambiguous provider failures; the coordinator also enforces a total ceiling.
    """

    @abstractmethod
    def make(self, attempt: int, feedback: RepairFeedback,
             remaining_cost_usd: float) -> PatchProposer:
        """Return a fresh, single-use proposer for the requested attempt."""

    @property
    def requires_budget_reservation(self) -> bool:
        """Whether this proposer may send paid model calls (fail-closed default)."""
        return True

    @property
    def accounted_cost_usd(self) -> float:
        """Report cumulative reserved or actual model spending; default zero."""
        return 0.0


class ScriptedRepairFactory(RepairProposerFactory):
    """Deterministic mock factory for offline tests, no provider network calls."""

    def __init__(self, patches: tuple[str, ...]):
        if not 1 <= len(patches) <= 5:
            raise ValueError('Provide one to five predetermined patches')
        self.patches = patches
        self.feedback_seen: list[RepairFeedback] = []

    @property
    def requires_budget_reservation(self) -> bool:
        """Offline deterministic fixtures need no model budget reservation."""
        return False

    def make(self, attempt: int, feedback: RepairFeedback,
             remaining_cost_usd: float) -> PatchProposer:
        """Select a fixture while retaining provenance of the previous failure."""
        self.feedback_seen.append(feedback)
        if not 1 <= attempt <= len(self.patches):
            raise RuntimeError('No scripted proposal available for attempt')
        return PatchFileProposer(self.patches[attempt - 1])


class FeedbackPatchProposer(PatchProposer):
    """Add bounded, host-derived retry feedback to an ordinary patch proposer."""

    def __init__(self, inner: PatchProposer, feedback: RepairFeedback):
        self.inner, self.feedback = inner, feedback

    def propose(self, task: str, source_files: dict[str, str]):
        """Request a fresh full patch against the *original* source snapshot."""
        if self.feedback.failure_kind:
            task += ('\n\nPrior attempt did not pass independent checks. '
                     'Propose a complete replacement patch against the supplied original files, '
                     'not an incremental patch. Host feedback: ' +
                     json.dumps(self.feedback.to_dict(), sort_keys=True))
        if len(task) > 8000:
            raise ValueError('Task plus bounded repair feedback exceeds size limit')
        return self.inner.propose(task, source_files)


class ModelRepairFactory(RepairProposerFactory):
    """Optional cost-aware bridge to the existing JSON model transport port.

    A new :class:`ModelPatchProposer` is instantiated for each attempt, with
    the remaining total spending cap. Unknown provider usage is charged at the
    full reserved bound rather than treated as free.
    """

    def __init__(self, transport, pricing, *, max_output_tokens: int = 2048, context=None):
        self.transport, self.pricing = transport, pricing
        self.max_output_tokens, self.context = max_output_tokens, context
        self._proposers: list = []

    def make(self, attempt: int, feedback: RepairFeedback,
             remaining_cost_usd: float) -> PatchProposer:
        """Create an individually bounded remote proposer for one paid call."""
        from .code_provider import ModelPatchProposer
        from .model_planner import ModelLimits, ModelBudgetExceeded
        if remaining_cost_usd <= 0:
            raise ModelBudgetExceeded('No remaining approved provider budget')
        proposer = ModelPatchProposer(self.transport, self.pricing,
            ModelLimits(max_calls=1, max_output_tokens_per_call=self.max_output_tokens,
                        max_cost_usd=remaining_cost_usd), context=self.context)
        self._proposers.append(proposer)
        return FeedbackPatchProposer(proposer, feedback)

    @property
    def accounted_cost_usd(self) -> float:
        """Sum reserved/actual expenditures, including ambiguous failed calls."""
        return sum(x.accounted_cost_usd for x in self._proposers)


def _request_digest(*, project_id: str, base_commit: str, task: str, contract: TaskContract,
                    sources: tuple[str, ...], limits: RepairLimits, timeout_seconds: int,
                    require_red_test: bool = False) -> str:
    payload = {'project_id': project_id, 'base_commit': base_commit,
               'task_sha256': hashlib.sha256(task.encode()).hexdigest(),
               'contract_sha256': contract.sha256, 'sources': list(sources),
               'max_attempts': limits.max_attempts, 'max_cost_usd': limits.max_model_cost_usd,
               'timeout_seconds': timeout_seconds}
    if require_red_test:
        payload['require_red_test'] = True
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _classify(outcome: CodingOutcome, repository: SQLiteRepository) -> tuple[str, RepairFeedback]:
    events = repository.events(outcome.run_id)
    integrity = [x['payload'] for x in events if x['event'] == 'code_post_test_integrity']
    if len(integrity) != 1 or not integrity[0].get('unchanged'):
        return 'blocked', RepairFeedback()
    failed = tuple(i for i, r in enumerate(outcome.tests) if r.exit_code != 0 or r.timed_out)
    if failed:
        return 'failed_test', RepairFeedback('failed_test', failed, outcome.patch_sha256)
    if outcome.contract_verified:
        return 'passed', RepairFeedback()
    checks = [e['payload'] for e in events if e['event'] == 'code_contract_check']
    if len(checks) == 1 and not checks[0].get('passed'):
        return 'failed_contract', RepairFeedback('failed_contract', (), outcome.patch_sha256)
    if outcome.tests and all(r.kind == 'mock' and r.exit_code == 0 and not r.output_truncated
                             for r in outcome.tests):
        return 'mock_passed', RepairFeedback()
    return 'blocked', RepairFeedback()


def _finish_status(journal: SQLiteRepairJournal, state: RepairState) -> RepairState:
    if state.status != 'running' or not state.attempts:
        return state
    last = state.attempts[-1].status
    if last == 'passed':
        return journal.close(state.session_id, 'verified')
    if last == 'mock_passed':
        return journal.close(state.session_id, 'needs_real_tests')
    if last == 'blocked':
        return journal.close(state.session_id, 'blocked')
    if len(state.attempts) >= state.max_attempts:
        return journal.close(state.session_id, 'exhausted')
    return state


def run_repair_session(*, idempotency_key: str, project_path: Path, state_root: Path,
                       repository: SQLiteRepository, task: str, contract: TaskContract,
                       proposer_factory: RepairProposerFactory, runner: IsolatedTestRunner,
                       limits: RepairLimits, source_targets: tuple[str, ...] = (),
                       approved_writes: bool = False, timeout_seconds: int = 120,
                       resume: bool = False, patch_policy: PatchPolicy | None = None,
                       require_red_test: bool = False) -> RepairState:
    """Execute up to N independent worktrees and checkpoint every attempt.

    The caller must authorize workspace writes and provide the independent
    acceptance contract. If process death interrupts a reserved attempt,
    subsequent calls require ``resume=True`` and reconstruct only fully
    recorded, intact previous runs. Incomplete/ambiguous attempts are blocked.
    No user checkout is changed and no patch is merged automatically.

    A successful *mock* sequence reports ``needs_real_tests``, never verified.
    A real sequence can report ``verified`` for the pinned contract, but does
    not assert semantic completion of any unstated requirements.
    """
    if type(require_red_test) is not bool:
        raise ValueError('require_red_test must be a boolean')
    if not approved_writes:
        raise PermissionError('Repair requires explicit write approval')
    if not isinstance(contract, TaskContract) or not isinstance(proposer_factory, RepairProposerFactory):
        raise TypeError('Contract and proposer factory must implement published ports')
    if not isinstance(runner, IsolatedTestRunner):
        raise TypeError('Runner must implement the isolated test port')
    if not 1 <= len(task) <= 7600 or not 1 <= timeout_seconds <= 600:
        raise ValueError('Invalid task or per-command timeout')
    if not isinstance(limits, RepairLimits):
        raise TypeError('Invalid repair limits')
    project = load_project(project_path, repository)
    root, base_commit = GitWorktreeManager.check_source(project_path)
    policy = patch_policy or PatchPolicy()
    contract.preflight(root, policy, contract.test_commands)
    if any(s not in contract.allowed_paths for s in source_targets):
        raise ValueError('Source targets outside approved scope')
    digest = _request_digest(project_id=project.project_id, base_commit=base_commit,
                             task=task, contract=contract, sources=source_targets,
                             limits=limits, timeout_seconds=timeout_seconds,
                             require_red_test=require_red_test)
    journal = SQLiteRepairJournal(repository, state_root)
    state, fresh = journal.open(idempotency_key=idempotency_key, request_sha256=digest,
                                project_id=project.project_id, contract_sha256=contract.sha256,
                                max_attempts=limits.max_attempts)
    if not fresh:
        if state.status != 'running':
            return state
        if not resume:
            raise RepairConflict('Existing running session; use explicit resume')
        state = journal.reconcile(state.session_id)
        state = _finish_status(journal, state)
        if state.status != 'running':
            return state
    started = time.monotonic()
    feedback = RepairFeedback.from_dict(state.feedback)
    while state.status == 'running':
        if len(state.attempts) >= limits.max_attempts or time.monotonic() - started >= limits.max_wall_seconds:
            return journal.close(state.session_id, 'exhausted')
        remaining = limits.max_model_cost_usd - state.cost_usd
        if proposer_factory.accounted_cost_usd > remaining + 1e-9:
            return journal.close(state.session_id, 'exhausted')
        if proposer_factory.requires_budget_reservation and remaining <= 0:
            return journal.close(state.session_id, 'exhausted')
        # Pre-reserve all remaining provider spending. An interrupted attempt
        # cannot unknowingly spend the same USD again after restart.
        entry = journal.reserve(state.session_id,
            cost_reservation_usd=max(0.0, remaining) if proposer_factory.requires_budget_reservation else 0.0)
        cost_before = proposer_factory.accounted_cost_usd
        try:
            proposer = proposer_factory.make(entry.number, feedback, max(0.0, remaining))
            if not isinstance(proposer, PatchProposer):
                raise TypeError('Invalid patch proposer returned by repair factory')
            outcome = run_coding_task(project_path=root, state_root=state_root, repository=repository,
                                      run_id=entry.run_id, task=task,
                                      proposer=proposer, runner=runner,
                                      test_commands=contract.test_commands,
                                      approved_writes=True, patch_policy=policy,
                                      source_targets=source_targets,
                                      timeout_seconds=timeout_seconds, contract=contract,
                                      require_red_test=require_red_test)
            kind, feedback = _classify(outcome, repository)
            delta = proposer_factory.accounted_cost_usd - cost_before
            summary = {'contract_verified': outcome.contract_verified,
                       'acceptance_verified': outcome.acceptance_verified,
                       'test_exit_codes': [r.exit_code for r in outcome.tests],
                       'patch_sha256': outcome.patch_sha256}
            state = journal.finish_attempt(state.session_id, entry.run_id, status=kind,
                                           summary=summary, cost_delta=delta,
                                           feedback=feedback.to_dict() if feedback.failure_kind else {})
            if state.cost_usd > limits.max_model_cost_usd + 1e-8:
                return journal.close(state.session_id, 'exhausted')
            state = _finish_status(journal, state)
        except Exception as exc:
            # Exceptions are terminal. In particular, unsafe patches, ambiguous
            # provider calls, and untrusted sandbox failures are never retried.
            delta = max(0.0, proposer_factory.accounted_cost_usd - cost_before)
            journal.finish_attempt(state.session_id, entry.run_id, status='blocked',
                                   summary={'error_type': type(exc).__name__}, cost_delta=delta)
            return journal.close(state.session_id, 'blocked')
    return state
