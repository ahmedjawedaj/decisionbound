"""Child-process entrypoint that lets the offline preparation worker be killed.

``PreparationWorker`` normally runs inside a supervisor thread, and a thread
cannot be stopped. This module is what a :class:`~decisionbound.containment.ContainedWorker`
runs instead, so the same preparation can be killed on a deadline, on cancel and
after a coordinator crash.

Protocol (see ``site_docs/worker-containment.md``): one JSON assignment on stdin,
one JSON receipt line on stdout, nothing else. The assignment carries no owner
token and no generation. The receipt is still checked again by the host through
``IndependentTaskVerifier``, which recomputes the capsule itself.

This module grants no model, network or project write permission. It only runs
the ``prepare`` kind and refuses every other kind. Like the host, it opens the
state database, which can commit the schema version, so the child needs SQLite's
write lock briefly at start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .containment import ContainedWorker, ProcessRegistry, ResourceLimits
from .storage import SQLiteRepository
from .workflows import PreparationWorker, TaskAssignment, TaskHandoff, WorkflowTask

#: Largest assignment the child will read, far above any valid bounded task.
MAX_ASSIGNMENT_BYTES = 64_000


def _assignment(raw: bytes) -> TaskAssignment:
    """Rebuild a typed assignment from stdin, rejecting anything malformed."""
    if len(raw) > MAX_ASSIGNMENT_BYTES:
        raise ValueError("Assignment is too large")
    value: Any = json.loads(raw.decode("utf-8"))
    expected = {"workflow_id", "project_id", "project_path", "task", "attempt",
                "reserved_cost_usd", "handoffs"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Assignment has unknown or missing fields")
    handoffs = value["handoffs"]
    if not isinstance(handoffs, list) or len(handoffs) > 16:
        raise ValueError("Handoffs must be a short array")
    text = ("workflow_id", "project_id", "project_path")
    cost = value["reserved_cost_usd"]
    if (any(not isinstance(value[k], str) or not value[k] for k in text) or
            type(value["attempt"]) is not int or not 1 <= value["attempt"] <= 100 or
            isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 <= cost < 10**9):
        raise ValueError("Assignment has invalid field types")
    for h in handoffs:
        if (not isinstance(h, dict) or set(h) != {"task_id", "kind", "receipt_sha256", "verified"} or
                any(not isinstance(h[k], str) for k in ("task_id", "kind", "receipt_sha256")) or
                not isinstance(h["verified"], bool)):
            raise ValueError("Handoff has invalid fields")
    return TaskAssignment(
        workflow_id=value["workflow_id"], project_id=value["project_id"],
        project_path=Path(value["project_path"]), task=WorkflowTask.from_dict(value["task"]),
        attempt=value["attempt"], reserved_cost_usd=float(cost),
        handoffs=tuple(TaskHandoff(task_id=h["task_id"], kind=h["kind"],
                                   receipt_sha256=h["receipt_sha256"], verified=h["verified"])
                       for h in handoffs))


def child_can_import() -> bool:
    """Return True when ``python -I -m decisionbound.worker_child`` can start here.

    The child discards stderr, so a missing install would otherwise show up only
    as failed tasks. Isolated mode also ignores user site-packages, so a
    ``pip install --user`` is not enough.
    """
    import subprocess
    try:
        done = subprocess.run([sys.executable, "-I", "-c", "import decisionbound.worker_child"],
                              capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def run(database: Path, stdin: bytes) -> dict[str, Any]:
    """Prepare one task and return the receipt as a plain dictionary."""
    assignment = _assignment(stdin)
    if assignment.task.kind != "prepare":
        raise PermissionError("The preparation child only runs the prepare kind")
    receipt = PreparationWorker(SQLiteRepository(database)).execute(assignment)
    return {"status": receipt.status, "evidence_sha256": receipt.evidence_sha256}


def main(argv: Sequence[str] | None = None) -> int:
    """Read one assignment, print one receipt line. Failures print nothing to stdout."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--database":
        print("usage: python -I -m decisionbound.worker_child --database PATH", file=sys.stderr)
        return 2
    try:
        receipt = run(Path(args[1]), sys.stdin.buffer.read(MAX_ASSIGNMENT_BYTES + 1))
    except Exception:  # noqa: BLE001 - the host treats any non-receipt as a crash, details stay private
        return 1
    sys.stdout.write(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


def contained_preparation_worker(registry: ProcessRegistry, *, deadline_seconds: float = 60,
                                 term_grace_seconds: float = 3,
                                 limits: ResourceLimits | None = None) -> ContainedWorker:
    """Build a killable worker for the offline ``prepare`` kind.

    The argv is fixed here and uses the interpreter running the host, in isolated
    mode, so it ignores ``PYTHON*`` variables and the project directory on the
    import path. The package must be installed for ``-m`` to find it. The state
    database is the registry's own, opened by the child at the path it was given.
    """
    database = registry.store.path
    return ContainedWorker([sys.executable, "-I", "-m", "decisionbound.worker_child",
                            "--database", str(Path(database).resolve())],
                           registry=registry, deadline_seconds=deadline_seconds,
                           term_grace_seconds=term_grace_seconds, limits=limits,
                           require_strong=True)


if __name__ == "__main__":
    raise SystemExit(main())
