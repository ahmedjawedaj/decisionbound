"""Bounded local execution with durable audit events and verification hooks.

This alpha implements an offline preparation worker, not a full coding agent.
It's deliberately designed so provider-specific workers can be swapped in
later without changing transport clients or weakening the verifier boundary.
"""
from __future__ import annotations
import uuid
import hashlib
from dataclasses import asdict
from .models import ContextCapsule, WorkerResult
from .ports import AgentWorker, TaskVerifier
from .storage import SQLiteRepository


class OfflinePreparationWorker(AgentWorker):
    """Useful, deterministic baseline for testing orchestration without keys."""

    def run(self, task: str, context: ContextCapsule) -> WorkerResult:
        """Prepare an evidence summary; does not perform code modifications."""
        summary = f"Prepared {len(context.items)} context items for task: {task}"
        return WorkerResult("prepared", summary, tuple(i.source for i in context.items))


class PreparationVerifier(TaskVerifier):
    """Verify that an offline worker prepared a budget-compliant capsule."""

    def verify(self, task: str, result: WorkerResult) -> bool:
        """Validate the result state without labeling it task completion."""
        return result.status == "prepared" and bool(task.strip()) and bool(result.message)


class Supervisor:
    """Run one bounded worker, audit it, and separate preparation from success.

    Args:
        repository: Shared event store.
        worker: Replaceable worker implementing :class:`AgentWorker`.
        verifier: Independent predicate, not the worker's self-report.

    Invariant:
        ``verified_preparation`` does not mean the user's task was completed.
    """
    def __init__(self, repository: SQLiteRepository, worker: AgentWorker | None = None,
                 verifier: TaskVerifier | None = None):
        self.repository = repository
        self.worker = worker or OfflinePreparationWorker()
        self.verifier = verifier or PreparationVerifier()

    def run(self, task: str, context: ContextCapsule, run_id: str | None = None, max_steps: int = 1) -> dict:
        """Execute bounded worker once, preserving error and verification traces."""
        if max_steps < 1 or max_steps > 25:
            raise ValueError("max_steps must be between 1 and 25")
        rid = run_id or f"run_{uuid.uuid4().hex}"
        if self.repository.events(rid):
            raise ValueError("Run ID already exists; retries require reconciliation")
        self.repository.record_event(rid, "started", {
            "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
            "task_chars": len(task), "context_tokens": context.estimated_tokens})
        try:
            result = self.worker.run(task, context)
            # The persistent ledger stores only hashes/counts, never raw file contents
            # from tool observations that may include personal or proprietary data.
            self.repository.record_event(rid, "worker_result", {
                "status": result.status,
                "message_sha256": hashlib.sha256(result.message.encode()).hexdigest(),
                "message_chars": len(result.message),
                "observation_count": len(result.observations),
                "observation_hashes": [hashlib.sha256(v.encode()).hexdigest() for v in result.observations]})
            verified = self.verifier.verify(task, result)
            self.repository.record_event(rid, "verification", {"preparation_verified": verified,
                                                                  "task_completed": False})
            return {"run_id": rid, "result": asdict(result), "preparation_verified": verified,
                    "task_completed": False}
        except Exception as exc:
            self.repository.record_event(rid, "failed", {"error_type": type(exc).__name__})
            raise
