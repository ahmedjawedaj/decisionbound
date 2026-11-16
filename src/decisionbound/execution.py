"""Audited, bounded model-to-tool execution that never claims coding completion.

This module connects model planning, tool permissions, immutable task context,
SQLite traces and an independent read-only evidence verifier. Every request is
explicitly scoped to one initialized project and a per-run budget.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from pathlib import Path

from .agent_loop import BoundedReadOnlyAgent, repository_readonly_tools
from .identity import load_project
from .model_planner import BudgetedModelPlanner, JSONModelTransport, ModelLimits, Pricing
from .models import WorkerResult
from .ports import TaskVerifier
from .runtime import Supervisor
from .services import DecisionService
from .storage import SQLiteRepository


class ReadOnlyEvidenceVerifier(TaskVerifier):
    """Check tool-observation provenance, not semantic task correctness.

    This verifier cannot certify that an arbitrary model explanation is true,
    and must never report code-change or user-task completion.
    """

    def verify(self, task: str, result: WorkerResult) -> bool:
        """Require a prepared result and at least one structured tool observation."""
        if not task.strip() or result.status != "prepared" or not result.observations:
            return False
        try:
            return all(isinstance(json.loads(item).get("tool"), str) and
                       isinstance(json.loads(item).get("result"), dict)
                       for item in result.observations)
        except (ValueError, AttributeError, TypeError):
            return False


class ReadOnlyLedgerVerifier(ReadOnlyEvidenceVerifier):
    """Cross-check worker evidence against independent host-side tool receipts.

    This verifies that returned observations match tool executions recorded by
    the runtime, preventing a worker from fabricating tool evidence in its
    final answer. It does not verify semantic task correctness.
    """

    def __init__(self, repository: SQLiteRepository, run_id: str):
        self.repository, self.run_id = repository, run_id

    def verify(self, task: str, result: WorkerResult) -> bool:
        """Require matching SHA-256 hashes and counts in the durable ledger."""
        if not super().verify(task, result):
            return False
        events = [e["payload"] for e in self.repository.events(self.run_id)
                  if e["event"] == "tool_observation"]
        if len(events) != len(result.observations):
            return False
        for event, observation in zip(events, result.observations):
            if (event.get("truncated") or event.get("size") != len(observation) or
                    event.get("sha256") != hashlib.sha256(observation.encode()).hexdigest()):
                return False
        return True


def run_readonly_agent(*, project_path: Path, store: SQLiteRepository,
                       task: str, transport: JSONModelTransport,
                       pricing: Pricing, limits: ModelLimits | None = None,
                       max_context_tokens: int = 1200,
                       allowed_tools: set[str] | None = None,
                       workflow_task_ref: str | None = None) -> dict:
    """Run a budgeted, model-driven read-only inspection with durable receipts.

    Args:
        project_path: Verified local project; no cross-project reads.
        store: Durable event and project registry.
        task: A user request for repository inspection, not code modification.
        transport: Explicit provider adapter or scripted deterministic fake.
        pricing: Explicit input/output prices. Never infer a remote model is free.
        limits: Per-run preflight limits; calls are accounted conservatively.
        max_context_tokens: Budget for selected, approved repository context.
        allowed_tools: Subset of local read-only tools permitted for this run.

    Returns:
        A run receipt, usage estimate and trace reference. ``task_completed``
        remains false pending real objective-specific verification.
    """
    if not 1 <= len(task) <= 8000 or not 100 <= max_context_tokens <= 20_000:
        raise ValueError("Task or context limit invalid")
    project = load_project(project_path, store)
    tools = repository_readonly_tools(Path(project_path))
    names = allowed_tools if allowed_tools is not None else {"search_project", "read_file"}
    if not names <= {"search_project", "read_file"}:
        raise PermissionError("Unknown or non-read-only capability")
    capsule = DecisionService(store).prepare(task, project, max_context_tokens)
    rid = f"run_{uuid.uuid4().hex}"
    if workflow_task_ref is not None and not 1 <= len(workflow_task_ref) <= 200:
        raise ValueError("Invalid workflow task correlation reference")

    def record(event: str, payload: dict) -> None:
        """Append metadata-only observations while keeping raw sources local."""
        store.record_event(rid, event, payload)

    bounds = limits or ModelLimits()
    planner = BudgetedModelPlanner(transport, tools, names, pricing, bounds, record)
    worker = BoundedReadOnlyAgent(planner, tools, names, max_steps=bounds.max_calls,
                                  max_observation_chars=3000, on_event=record)
    try:
        output = Supervisor(store, worker, ReadOnlyLedgerVerifier(store, rid)).run(task, capsule, run_id=rid)
    except Exception as exc:
        # Keep the original exception type (permission/budget/schema) but give
        # operators a stable event-ledger handle for postmortem inspection.
        exc.add_note(f"DecisionBound run_id={rid}; inspect with dbound trace {rid}")
        raise
    if workflow_task_ref is not None:
        store.record_event(rid, "scope", {"project_id": project.project_id,
            "workflow_task_sha256": hashlib.sha256(workflow_task_ref.encode()).hexdigest()})
    output["model_accounting"] = vars(planner.accounting).copy()
    output["evidence_inspection_verified"] = bool(output["preparation_verified"])
    output["task_completed"] = False
    return output
