"""Independent, fail-closed review of completed coding stages.

This is an *evidence* review, not an LLM opinion or proof of semantic
correctness. A reviewer cannot promote mocked tests or an unchecked patch to a
verified outcome. Its source of truth is the protected host journal and the
private, checksum-validated patch artifact. Project owners can add veto-only
policy hooks, but not bypass canonical verification.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .code_journal import SQLiteRepairJournal
from .code_patches import PatchValidator, PatchPolicy
from .code_workspace import GitWorktreeManager, WorkspaceError
from .storage import SQLiteRepository


def _fingerprint(value: dict[str, Any]) -> str:
    """Fingerprint bounded, source-free evidence with deterministic JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ReviewBlocked(RuntimeError):
    """Required host-side evidence was missing, inconsistent, or unverifiable."""


@dataclass(frozen=True)
class CodeReviewEvidence:
    """Minimal review record; never stores source, prompts, or model output.

    The digest is tied to a particular code stage, repair session, immutable
    patch bytes, contract, verified test receipts, and checked-out base commit.
    ``task_completed`` intentionally remains false even when this stage passes.
    """

    workflow_id: str
    code_task_id: str
    code_receipt_sha256: str
    repair_session_id: str
    run_id: str
    patch_sha256: str
    contract_sha256: str
    base_commit: str
    tests_sha256: str

    @property
    def sha256(self) -> str:
        """Return a stable digest that the host verifier independently rebuilds."""
        return _fingerprint(vars(self))


class CodeReviewPolicy(ABC):
    """Veto-only review extension; it cannot grant permissions or certify tests.

    Override this for extra, deterministic checks such as an organization-wide
    maximum patch size. Returning ``True`` allows the host to continue its own
    safety checks; it does not declare the task complete.
    """

    @abstractmethod
    def accept(self, evidence: CodeReviewEvidence) -> bool:
        """Return false to block a candidate whose canonical evidence passed."""


class AllowReviewedEvidence(CodeReviewPolicy):
    """Default policy: enforce host evidence checks without extra vetoes."""

    def accept(self, evidence: CodeReviewEvidence) -> bool:
        """Allow host-validated evidence to pass to the independent verifier."""
        return True


class CodeReviewService:
    """Read-only, restart-safe evidence reviewer for an approved coding stage.

    Args:
        repository: Owner-controlled state ledger, never a model-generated DB.
        state_root: Private artifact directory containing saved patches.

    This service makes no network calls, invokes no model, and never replays a
    coding attempt. A real Docker test still needs verification on a connected
    host; mock receipts cannot be relabeled by this service.
    """

    def __init__(self, repository: SQLiteRepository, state_root: Path):
        self.repository = repository
        self.state_root = state_root.expanduser().resolve(strict=True)
        if not self.state_root.is_dir() or state_root.is_symlink():
            raise ReviewBlocked('Invalid review state directory')

    @staticmethod
    def _single(events: list[dict], name: str) -> dict:
        """Require exactly one host event rather than choosing an arbitrary one."""
        found = [event['payload'] for event in events if event['event'] == name]
        if len(found) != 1:
            raise ReviewBlocked('Missing or ambiguous host ' + name + ' receipt')
        return found[0]

    def examine(self, *, workflow_id: str, project_id: str, project_path: Path,
                code_task_id: str, expected_receipt_sha256: str) -> CodeReviewEvidence:
        """Rebuild the canonical code-review receipt without executing user code.

        The upstream task must have been host-verified in the *same* workflow.
        A matching code idempotency key associates the repair journal with the
        task, so workers cannot submit unrelated successful sessions as proof.
        """
        from .workflows import _code_idempotency  # local import avoids a cycle

        with self.repository._connection() as db:
            workflow = db.execute('SELECT project_id,project_path FROM workflows WHERE workflow_id=?',
                                  (workflow_id,)).fetchone()
            task = db.execute('SELECT state,receipt_sha256 FROM workflow_tasks WHERE workflow_id=? AND task_id=?',
                              (workflow_id, code_task_id)).fetchone()
            repair = db.execute('''SELECT id,project_id,idempotency_key FROM repair_sessions
                                   WHERE idempotency_key=?''',
                                (_code_idempotency(workflow_id, code_task_id),)).fetchone()
        if (workflow is None or workflow['project_id'] != project_id or
                Path(workflow['project_path']).resolve(strict=True) != project_path.resolve(strict=True) or
                task is None or task['state'] != 'verified' or
                task['receipt_sha256'] != expected_receipt_sha256 or
                not expected_receipt_sha256 or repair is None or repair['project_id'] != project_id):
            raise ReviewBlocked('Stage/project provenance mismatch or not independently verified')

        state = SQLiteRepairJournal(self.repository, self.state_root).get(repair['id'])
        if (state.status != 'verified' or not state.contract_sha256 or not state.attempts or
                state.attempts[-1].status != 'passed'):
            raise ReviewBlocked('Coding attempt did not pass a real approved contract')
        attempt = state.attempts[-1]
        events = self.repository.events(attempt.run_id)
        if any(event['event'] == 'code_failed' for event in events):
            raise ReviewBlocked('Coding attempt contains failure events')
        started = self._single(events, 'code_started')
        workspace = self._single(events, 'code_worktree_created')
        patch = self._single(events, 'code_patch_applied')
        integrity = self._single(events, 'code_post_test_integrity')
        contract = self._single(events, 'code_contract_check')
        verification = self._single(events, 'code_verification')
        finished = self._single(events, 'code_finished')
        cleaned = self._single(events, 'code_workspace_cleaned')
        tests = [event['payload'] for event in events if event['event'] == 'code_test_receipt']
        if (started.get('project_id') != project_id or not started.get('write_approved') or
                started.get('contract_sha256') != state.contract_sha256 or
                contract.get('contract_sha256') != state.contract_sha256 or
                finished.get('contract_sha256') != state.contract_sha256 or
                not contract.get('passed') or not verification.get('contract_verified') or
                not finished.get('contract_verified') or not finished.get('acceptance_verified') or
                not integrity.get('unchanged') or
                integrity.get('diff_sha256') != patch.get('diff_sha256') or
                cleaned.get('ok') is not True or len(tests) != started.get('test_count') or
                not tests or any(test.get('kind') != 'docker' or test.get('exit_code') != 0 or
                    test.get('timed_out') or len(test.get('receipt_sha256', '')) != 64 for test in tests)):
            raise ReviewBlocked('Incomplete host verification or simulated tests')
        baseline = None
        if started.get('requires_red_test'):
            baseline = self._single(events, 'code_prepatch_test_receipt')
            if (baseline.get('kind') != 'docker' or type(baseline.get('exit_code')) is not int or
                    baseline['exit_code'] == 0 or baseline.get('timed_out') or
                    baseline.get('output_truncated') or len(baseline.get('receipt_sha256', '')) != 64 or
                    baseline.get('command_sha256') != tests[0].get('command_sha256')):
                raise ReviewBlocked('Falling-baseline regression evidence missing or invalid')
        if (attempt.summary.get('patch_sha256') != patch.get('sha256') or
                attempt.summary.get('contract_verified') is not True):
            raise ReviewBlocked('Repair checkpoint contradicts coding receipts')

        artifact_dir = self.state_root / 'code-artifacts'
        artifact = artifact_dir / (attempt.run_id + '.patch')
        if (artifact_dir.is_symlink() or artifact.is_symlink() or not artifact.is_file() or
                artifact.stat().st_size > 250_000):
            raise ReviewBlocked('Private patch artifact missing or unsafe')
        content = artifact.read_bytes()
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != patch.get('sha256') or len(content) != patch.get('size_bytes'):
            raise ReviewBlocked('Patch artifact failed byte-level integrity')
        try:
            parsed = PatchValidator(PatchPolicy()).inspect(content.decode('utf-8'), project_path)
        except (UnicodeError, ValueError, OSError) as exc:
            raise ReviewBlocked('Patch artifact does not pass independent safety parser') from exc
        if parsed.sha256 != actual_sha or list(parsed.paths) != patch.get('paths'):
            raise ReviewBlocked('Patch scope does not match host receipt')
        try:
            _, head = GitWorktreeManager.check_source(project_path)
        except (WorkspaceError, OSError) as exc:
            raise ReviewBlocked('Source checkout is no longer clean') from exc
        if head != workspace.get('base_commit'):
            raise ReviewBlocked('Source base commit moved since accepted coding attempt')
        return CodeReviewEvidence(workflow_id, code_task_id, expected_receipt_sha256,
                                  state.session_id, attempt.run_id, actual_sha, state.contract_sha256,
                                  head, _fingerprint({'receipts': tests, 'red': baseline}))
