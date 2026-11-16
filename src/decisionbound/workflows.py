"""Durable local DAG orchestration with leases, budget reservations and evidence gates.

The scheduler operates on metadata and user-approved, immutable task specifications.
Only explicitly registered workers execute tasks.  A mock result can never be
mistaken for independently verified code execution.  SQLite is suitable for
one-machine processes, not distributed consensus or remote multi-tenancy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Mapping
import uuid

from .identity import load_project
from .services import DecisionService
from .storage import SQLiteRepository

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FINISHED = frozenset({"verified", "mock_passed", "needs_real_tests", "blocked", "cancelled", "failed"})
_NON_SUCCESS = frozenset({"mock_passed", "needs_real_tests", "blocked", "cancelled", "failed"})
_ALLOWED_KINDS = frozenset({"prepare", "inspect", "mock", "code", "review"})
_ROLES = frozenset({"general", "planner", "researcher", "implementer", "reviewer", "tester"})


class WorkflowConflict(RuntimeError):
    """A claim, budget, project identity or workflow revision is no longer valid."""


def _digest(value: Any) -> str:
    """Hash canonical non-secret JSON for stable identity and evidence comparison."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _usd_micros(value: float) -> int:
    """Convert bounded USD values to integer microdollars without floating drift."""
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Invalid USD budget")
    return round(value * 1_000_000)


@dataclass(frozen=True)
class WorkflowTask:
    """Bounded task contract; ``kind`` determines the host's verification policy.

    ``description`` is stored in the local private DB for durable resume; never
    include credentials. It is treated as untrusted task input, not a system prompt.
    Paid adapters must honor the reserved per-task budget before network calls.
    """
    task_id: str
    kind: str
    description: str
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 1
    max_cost_usd: float = 0.0
    max_context_tokens: int = 1200
    role: str = "general"

    def __post_init__(self) -> None:
        if (not isinstance(self.task_id, str) or not _ID.fullmatch(self.task_id) or
            not isinstance(self.kind, str) or self.kind not in _ALLOWED_KINDS or
            not isinstance(self.description, str) or self.role not in _ROLES or
            not 1 <= len(self.description) <= 3000 or
            not isinstance(self.depends_on, tuple) or len(self.depends_on) > 16 or
            len(set(self.depends_on)) != len(self.depends_on) or
            not all(isinstance(x, str) and _ID.fullmatch(x) for x in self.depends_on) or
            not 1 <= self.max_attempts <= 4 or not 100 <= self.max_context_tokens <= 20000):
            raise ValueError("Invalid bounded workflow task")
        _usd_micros(self.max_cost_usd)
        if self.kind == "code" and self.max_attempts > 1:
            raise ValueError("Write-capable tasks cannot be auto-retried by the dispatcher")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowTask":
        """Reject unknown keys and malformed fields rather than guessing intent."""
        if not isinstance(value, dict):
            raise ValueError("Task must be an object")
        allowed = {"task_id", "kind", "description", "depends_on", "max_attempts", "max_cost_usd", "max_context_tokens", "role"}
        if set(value) - allowed or not {"task_id", "kind", "description"} <= set(value):
            raise ValueError("Task has unknown or missing fields")
        if not isinstance(value.get("depends_on", []), list) or any(type(x) is not str for x in value.get("depends_on", [])):
            raise ValueError("Dependencies must be an array of IDs")
        if (type(value.get("max_attempts", 1)) is not int or
                type(value.get("max_context_tokens", 1200)) is not int):
            raise ValueError("Attempt and token limits must be integers")
        return cls(task_id=value["task_id"], kind=value["kind"], description=value["description"],
                   depends_on=tuple(value.get("depends_on", [])), max_attempts=value.get("max_attempts", 1),
                   max_cost_usd=value.get("max_cost_usd", 0), max_context_tokens=value.get("max_context_tokens", 1200),
                   role=value.get("role", "general"))

    def to_dict(self) -> dict:
        """Export a stable task specification without runtime secrets or leases."""
        return dict(task_id=self.task_id, kind=self.kind, description=self.description,
                    depends_on=list(self.depends_on), max_attempts=self.max_attempts,
                    max_cost_usd=self.max_cost_usd, max_context_tokens=self.max_context_tokens, role=self.role)


@dataclass(frozen=True)
class WorkflowSpec:
    """Finite DAG with a shared budget and explicit execution concurrency."""
    tasks: tuple[WorkflowTask, ...]
    max_workers: int = 2
    max_total_cost_usd: float = 0.0
    approval_sha256: str | None = None

    def __post_init__(self) -> None:
        if (not 1 <= len(self.tasks) <= 50 or not 1 <= self.max_workers <= 8 or
                len({t.task_id for t in self.tasks}) != len(self.tasks)):
            raise ValueError("Invalid graph size, concurrency or duplicate task IDs")
        _usd_micros(self.max_total_cost_usd)
        if self.approval_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.approval_sha256):
            raise ValueError("Invalid immutable workflow approval fingerprint")
        nodes = {t.task_id: t for t in self.tasks}
        visited, visiting = set(), set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("Cyclic workflow dependency")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in nodes[task_id].depends_on:
                if dep not in nodes:
                    raise ValueError(f"Unknown dependency {dep}")
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)
        for key in nodes:
            visit(key)
        for task in self.tasks:
            if task.kind == "review" and (task.role != "reviewer" or len(task.depends_on) != 1 or
                                          nodes[task.depends_on[0]].kind != "code" or
                                          _usd_micros(task.max_cost_usd) != 0):
                raise ValueError("Review stages require one code predecessor, reviewer role, and no model spending")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowSpec":
        """Parse a fully specified workflow without running any requested action."""
        if not isinstance(value, dict) or set(value) - {"schema_version", "tasks", "max_workers", "max_total_cost_usd", "approval_sha256"}:
            raise ValueError("Unrecognized workflow fields")
        if value.get("schema_version") != 1 or not isinstance(value.get("tasks"), list):
            raise ValueError("Workflow schema_version must be 1 with tasks array")
        if type(value.get("max_workers", 2)) is not int:
            raise ValueError("max_workers must be an integer")
        return cls(tuple(WorkflowTask.from_dict(t) for t in value["tasks"]),
                   value.get("max_workers", 2), value.get("max_total_cost_usd", 0.0),
                   value.get("approval_sha256"))

    def to_dict(self) -> dict:
        """Serialize as a versioned, portable workflow manifest."""
        value = {"schema_version": 1, "tasks": [t.to_dict() for t in self.tasks],
                 "max_workers": self.max_workers, "max_total_cost_usd": self.max_total_cost_usd}
        if self.approval_sha256 is not None:
            value["approval_sha256"] = self.approval_sha256
        return value


@dataclass(frozen=True)
class TaskLease:
    """Claimed task bound to one private owner token and monotonically increasing generation."""
    workflow_id: str
    project_id: str
    project_path: Path
    task: WorkflowTask
    attempt: int
    generation: int
    owner_token: str
    reserved_cost_usd: float
    handoffs: tuple["TaskHandoff", ...] = ()

    def assignment(self) -> "TaskAssignment":
        """Strip claim token before handing work to a potentially untrusted plugin."""
        return TaskAssignment(self.workflow_id, self.project_id, self.project_path,
                              self.task, self.attempt, self.reserved_cost_usd, self.handoffs)


@dataclass(frozen=True)
class TaskHandoff:
    """Small immutable handoff manifest from a verified prerequisite stage.

    Only host-origin receipt fingerprints cross the boundary, not another
    agent's raw reasoning, entire tool history, workspace or credentials.
    """
    task_id: str
    kind: str
    receipt_sha256: str
    verified: bool = True


@dataclass(frozen=True)
class TaskAssignment:
    """Least-privileged worker input; no lease owner token or generation."""
    workflow_id: str
    project_id: str
    project_path: Path
    task: WorkflowTask
    attempt: int
    reserved_cost_usd: float
    handoffs: tuple[TaskHandoff, ...] = ()


@dataclass(frozen=True)
class WorkerReceipt:
    """Worker output, untrusted until independently checked by the host verifier.

    ``status`` is advisory. Raw model output, secrets, test stdout and source code
    must not be persisted here; use private artifacts and independent receipts.
    """
    status: str
    evidence_sha256: str = ""
    repair_session_id: str = ""
    nested_run_id: str = ""
    cost_usd: float = 0.0


class WorkflowWorker(ABC):
    """Overridable per-kind worker; cannot claim or finalize tasks directly."""

    @abstractmethod
    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Perform a bounded task and return a source-free completion receipt."""


class PreparationWorker(WorkflowWorker):
    """Offline, read-only task preparation using the existing context engine."""

    def __init__(self, store: SQLiteRepository):
        self.store = store

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Generate a task capsule; no remote model, shell or network needed."""
        project = load_project(lease.project_path, self.store)
        if project.project_id != lease.project_id:
            raise PermissionError("Workflow project identity changed")
        capsule = DecisionService(self.store).prepare(lease.task.description, project,
                                                       lease.task.max_context_tokens)
        return WorkerReceipt("prepared", _digest(capsule.to_dict()))


class ModelInspectionWorker(WorkflowWorker):
    """Bounded model-driven read-only task worker from Sprint 03.

    The caller injects a fresh JSON transport factory and explicit USD pricing.
    No default network is made and paid transports require reserved budgets.
    Each task writes a separate read-only event ledger whose host evidence is
    checked again by :class:`IndependentTaskVerifier` before handoff.
    """

    def __init__(self, *, repository: SQLiteRepository, transport_factory,
                 pricing, allowed_tools: set[str] | None = None):
        self.repository = repository
        self.transport_factory = transport_factory
        self.pricing = pricing
        self.allowed_tools = allowed_tools or {"search_project", "read_file"}
        if not self.allowed_tools <= {"search_project", "read_file"}:
            raise PermissionError("Only read-only repository inspection tools are supported")

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Run the isolated model/tool loop under this task's spend reservation."""
        from .execution import run_readonly_agent
        from .model_planner import JSONModelTransport, ModelLimits, Pricing
        if lease.task.kind != "inspect" or not isinstance(self.pricing, Pricing):
            raise TypeError("Model inspection requires a typed inspection task and pricing")
        transport = self.transport_factory(lease)
        if not isinstance(transport, JSONModelTransport):
            raise TypeError("Transport factory must provide a JSONModelTransport")
        ref = f"{lease.workflow_id}:{lease.task.task_id}:{lease.attempt}"
        result = run_readonly_agent(project_path=lease.project_path, store=self.repository,
            task=lease.task.description, transport=transport, pricing=self.pricing,
            limits=ModelLimits(max_calls=4, max_input_tokens=20000, max_total_tokens=40000,
                max_output_tokens_per_call=512, max_cost_usd=lease.reserved_cost_usd,
                deadline_seconds=90),
            max_context_tokens=lease.task.max_context_tokens,
            allowed_tools=self.allowed_tools, workflow_task_ref=ref)
        if not result["preparation_verified"] or result["task_completed"]:
            raise RuntimeError("Nested inspection did not pass independent evidence check")
        return WorkerReceipt("inspected", nested_run_id=result["run_id"],
                             cost_usd=result["model_accounting"]["estimated_cost_usd"])


class ScriptedWorkflowWorker(WorkflowWorker):
    """Offline deterministic fake for testing dispatch; never verifies real work."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False):
        self.delay, self.fail = delay, fail

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Return an explicitly synthetic outcome after an optional test delay."""
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("scripted failure")
        return WorkerReceipt("mock_passed")


def _code_idempotency(workflow_id: str, task_id: str) -> str:
    """Build a bounded stable key within Sprint 05 repair journal limits."""
    return "wfcode_" + _digest({"workflow": workflow_id, "task": task_id})[:40]


class CodingRepairWorker(WorkflowWorker):
    """Adapter to Sprint 05's bounded patch-repair engine (disabled by default).

    All contracts, proposers and isolated test runners are injected by the
    operator. This adapter does not grant write permission, merge patches or
    inherit network credentials from the supervisor.
    """

    def __init__(self, *, repository: SQLiteRepository, state_root: Path,
                 approved: Mapping[str, dict]):
        self.repository, self.state_root, self.approved = repository, state_root, approved

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Execute only an explicitly consented coding task via run_repair_session."""
        if lease.task.kind != "code" or lease.task.task_id not in self.approved:
            raise PermissionError("No independently approved coding contract for task")
        from .code_repair import run_repair_session
        cfg = self.approved[lease.task.task_id]
        if cfg["limits"].max_model_cost_usd > lease.reserved_cost_usd:
            raise PermissionError("Approved coding model budget exceeds reserved task cap")
        if cfg.get("approved_writes") is not True:
            raise PermissionError("Coding writes not approved")
        result = run_repair_session(idempotency_key=_code_idempotency(lease.workflow_id, lease.task.task_id),
                                    project_path=lease.project_path, state_root=self.state_root,
                                    repository=self.repository, task=lease.task.description,
                                    contract=cfg["contract"], proposer_factory=cfg["factory"],
                                    runner=cfg["runner"], limits=cfg["limits"],
                                    source_targets=tuple(cfg.get("source_targets", ())),
                                    approved_writes=True, timeout_seconds=cfg.get("timeout_seconds", 120),
                                    require_red_test=cfg.get("require_red_test", False))
        return WorkerReceipt("code_result", repair_session_id=result.session_id,
                             cost_usd=result.cost_usd)


class CodeEvidenceReviewWorker(WorkflowWorker):
    """Evidence-only reviewer for one verified coding predecessor.

    This worker does not ask an LLM whether the patch is correct. It reconstructs
    host receipts, validates the private patch bytes, confirms sandbox tests,
    and checks that the original source commit is unchanged. An optional custom
    :class:`CodeReviewPolicy` may veto but cannot certify a missing test.
    """

    def __init__(self, *, repository: SQLiteRepository, state_root: Path, policy=None):
        from .code_review import AllowReviewedEvidence, CodeReviewPolicy
        self.repository, self.state_root = repository, state_root
        self.policy = policy if policy is not None else AllowReviewedEvidence()
        if not isinstance(self.policy, CodeReviewPolicy):
            raise TypeError("Review policy must implement the approved veto interface")

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Return a digest of independently reconstructed real test evidence."""
        from .code_review import CodeReviewService, ReviewBlocked
        if lease.task.kind != "review" or len(lease.handoffs) != 1 or lease.handoffs[0].kind != "code":
            raise ReviewBlocked("Review requires exactly one verified coding handoff")
        upstream = lease.handoffs[0]
        evidence = CodeReviewService(self.repository, self.state_root).examine(
            workflow_id=lease.workflow_id, project_id=lease.project_id,
            project_path=lease.project_path, code_task_id=upstream.task_id,
            expected_receipt_sha256=upstream.receipt_sha256)
        if not self.policy.accept(evidence):
            raise ReviewBlocked("A review policy rejected the verified evidence")
        return WorkerReceipt("reviewed", evidence_sha256=evidence.sha256)


class IndependentTaskVerifier:
    """Classify stage receipts from host state, never from a model's success claim.

    A verified *preparation stage* is not a completed user request. For coding,
    the Sprint 05 repair journal must independently report approved real tests.
    Mock outcomes are always labeled non-verifying.
    """

    def __init__(self, repository: SQLiteRepository, state_root: Path | None = None):
        self.repository, self.state_root = repository, state_root

    def verify(self, lease: TaskLease, receipt: WorkerReceipt) -> str:
        """Return the only permitted final state for a claimed worker result."""
        if lease.task.kind == "mock":
            return "mock_passed" if receipt.status == "mock_passed" else "blocked"
        if lease.task.kind == "prepare":
            if receipt.status != "prepared":
                return "blocked"
            project = load_project(lease.project_path, self.repository)
            if project.project_id != lease.project_id:
                return "blocked"
            expected = DecisionService(self.repository).prepare(lease.task.description, project,
                                                                 lease.task.max_context_tokens)
            return "verified" if receipt.evidence_sha256 == _digest(expected.to_dict()) else "blocked"
        if lease.task.kind == "inspect":
            if receipt.status != "inspected" or not re.fullmatch(r"run_[0-9a-f]{32}", receipt.nested_run_id):
                return "blocked"
            records = self.repository.events(receipt.nested_run_id)
            ref = f"{lease.workflow_id}:{lease.task.task_id}:{lease.attempt}"
            scope = [e["payload"] for e in records if e["event"] == "scope"]
            started = [e["payload"] for e in records if e["event"] == "started"]
            verified = [e["payload"] for e in records if e["event"] == "verification"]
            observations = [e for e in records if e["event"] == "tool_observation"]
            worker = [e["payload"] for e in records if e["event"] == "worker_result"]
            if (len(scope) != 1 or len(started) != 1 or len(verified) != 1 or
                len(worker) != 1 or not observations or any(e["event"] == "failed" for e in records) or
                scope[0].get("project_id") != lease.project_id or
                scope[0].get("workflow_task_sha256") != hashlib.sha256(ref.encode()).hexdigest() or
                started[0].get("task_sha256") != hashlib.sha256(lease.task.description.encode()).hexdigest() or
                not verified[0].get("preparation_verified") or verified[0].get("task_completed") or
                worker[0].get("observation_count") != len(observations)):
                return "blocked"
            return "verified"
        if lease.task.kind == "review":
            if (receipt.status != "reviewed" or receipt.cost_usd != 0 or
                    self.state_root is None or len(lease.handoffs) != 1):
                return "blocked"
            upstream = lease.handoffs[0]
            if upstream.kind != "code":
                return "blocked"
            from .code_review import CodeReviewService, ReviewBlocked
            try:
                evidence = CodeReviewService(self.repository, self.state_root).examine(
                    workflow_id=lease.workflow_id, project_id=lease.project_id,
                    project_path=lease.project_path, code_task_id=upstream.task_id,
                    expected_receipt_sha256=upstream.receipt_sha256)
            except (ReviewBlocked, OSError, ValueError, KeyError):
                return "blocked"
            return "verified" if receipt.evidence_sha256 == evidence.sha256 else "blocked"
        if lease.task.kind == "code":
            if not receipt.repair_session_id or self.state_root is None:
                return "blocked"
            from .code_journal import SQLiteRepairJournal
            try:
                repair = SQLiteRepairJournal(self.repository, self.state_root).get(receipt.repair_session_id)
            except (KeyError, ValueError):
                return "blocked"
            with self.repository._connection() as db:
                origin = db.execute("SELECT project_id,idempotency_key FROM repair_sessions WHERE id=?",
                                    (repair.session_id,)).fetchone()
            if (origin is None or origin["project_id"] != lease.project_id or
                    origin["idempotency_key"] != _code_idempotency(lease.workflow_id, lease.task.task_id)):
                return "blocked"
            return {"verified": "verified", "needs_real_tests": "needs_real_tests"}.get(repair.status, "blocked")
        return "blocked"


class WorkflowJournal:
    """SQLite-backed task ledger with CAS leases and durable budget reservations.

    Atomic ``BEGIN IMMEDIATE`` claims prevent duplicate scheduling by competing
    local processes. Expired read-only claims can be retried; expired code claims
    are quarantined because a write may have completed despite the timeout.
    Leases are not an execution sandbox and cannot forcibly stop a process.
    """

    def __init__(self, store: SQLiteRepository):
        self.store = store
        with store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL, project_id TEXT NOT NULL,
                    project_path TEXT NOT NULL, spec_json TEXT NOT NULL,
                    state TEXT NOT NULL, budget_micros INTEGER NOT NULL,
                    reserved_micros INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    workflow_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 0, owner_token TEXT,
                    lease_until REAL, receipt_sha256 TEXT, cost_micros INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT, PRIMARY KEY(workflow_id,task_id),
                    FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
                    task_id TEXT, event TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS workflow_events_workflow ON workflow_events(workflow_id,id);
                CREATE INDEX IF NOT EXISTS workflow_active_project ON workflows(project_id,state,created_at,workflow_id);
            """)

    @staticmethod
    def _event(db: sqlite3.Connection, workflow_id: str, task_id: str | None,
               event: str, metadata: dict | None = None) -> None:
        """Append bounded metadata in the same transaction as state changes."""
        payload = json.dumps(metadata or {}, sort_keys=True)
        if len(payload) > 1000:
            raise ValueError("Workflow event too large")
        db.execute("INSERT INTO workflow_events(workflow_id,task_id,event,metadata_json) VALUES (?,?,?,?)",
                   (workflow_id, task_id, event, payload))

    def create(self, *, project_path: Path, spec: WorkflowSpec,
               idempotency_key: str) -> str:
        """Register an immutable graph exactly once for one initialized project."""
        if not 8 <= len(idempotency_key) <= 128 or not _ID.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key (8–64 lowercase ID characters)")
        project = load_project(project_path, self.store)
        root = project_path.resolve(strict=True)
        payload = {"project_id": project.project_id, "path": str(root), "spec": spec.to_dict()}
        digest = _digest(payload)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prev = db.execute("SELECT workflow_id,request_sha256 FROM workflows WHERE idempotency_key=?",
                              (idempotency_key,)).fetchone()
            if prev:
                if prev["request_sha256"] != digest:
                    raise WorkflowConflict("Reused idempotency key has different request")
                return prev["workflow_id"]
            wf = "wf_" + uuid.uuid4().hex
            db.execute("INSERT INTO workflows(workflow_id,idempotency_key,request_sha256,project_id,project_path,spec_json,state,budget_micros) VALUES(?,?,?,?,?,?,?,?)",
                       (wf, idempotency_key, digest, project.project_id, str(root),
                        json.dumps(spec.to_dict(), sort_keys=True), "running", _usd_micros(spec.max_total_cost_usd)))
            for task in spec.tasks:
                db.execute("INSERT INTO workflow_tasks(workflow_id,task_id,state) VALUES(?,?,'pending')",
                           (wf, task.task_id))
            self._event(db, wf, None, "created", {"task_count": len(spec.tasks),
                                                    "graph_sha256": _digest(spec.to_dict())})
        return wf

    def list_active(self, *, project_id: str, limit: int = 50,
                    after: str | None = None) -> list[str]:
        """List only a project's unfinished graphs using a bounded fair cursor.

        ``after`` is a previously observed workflow ID. Keyset ordering is by
        ``(created_at,workflow_id)``, and selection wraps at the end. No global
        unbounded workflow scan is required as the local history grows.
        Callers must already resolve the project's trusted local identity.
        """
        if (not isinstance(project_id, str) or not project_id.startswith("prj_") or
                type(limit) is not int or not 1 <= limit <= 100 or
                after is not None and not isinstance(after, str)):
            raise ValueError("Invalid project scope, cursor or workflow limit")
        with self.store._connection() as db:
            marker = None
            if after is not None:
                marker = db.execute("SELECT created_at,workflow_id FROM workflows WHERE workflow_id=? AND project_id=?",
                                    (after, project_id)).fetchone()
            values = []
            if marker is not None:
                values = db.execute("""SELECT workflow_id FROM workflows WHERE project_id=? AND state='running'
                    AND (created_at>? OR (created_at=? AND workflow_id>?))
                    ORDER BY created_at,workflow_id LIMIT ?""",
                    (project_id, marker['created_at'], marker['created_at'], marker['workflow_id'], limit)).fetchall()
            if len(values) < limit:
                # Fill remaining batch from the beginning without duplicating
                # rows already selected past the keyset cursor.
                before = db.execute("""SELECT workflow_id FROM workflows WHERE project_id=? AND state='running'
                    ORDER BY created_at,workflow_id LIMIT ?""", (project_id, limit)).fetchall()
                seen = {r['workflow_id'] for r in values}
                for row in before:
                    if row['workflow_id'] not in seen and len(values) < limit:
                        values.append(row)
                        seen.add(row['workflow_id'])
        return [row['workflow_id'] for row in values]

    def _load(self, db: sqlite3.Connection, workflow_id: str) -> tuple[sqlite3.Row, WorkflowSpec, dict]:
        row = db.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        spec = WorkflowSpec.from_dict(json.loads(row["spec_json"]))
        states = {r["task_id"]: r for r in db.execute(
            "SELECT * FROM workflow_tasks WHERE workflow_id=?", (workflow_id,)).fetchall()}
        return row, spec, states

    def _refresh(self, db: sqlite3.Connection, workflow_id: str, now: float) -> None:
        """Reconcile expiry and failed prerequisites while holding write lock."""
        row, spec, states = self._load(db, workflow_id)
        if row["state"] != "running":
            return
        for task in spec.tasks:
            state = states[task.task_id]
            if state["state"] == "leased" and (state["lease_until"] or 0) <= now:
                if task.kind == "code":
                    next_state = "blocked"
                    reason = "unknown_write_outcome"
                elif task.kind in {"prepare", "inspect", "review"} and _usd_micros(task.max_cost_usd) == 0 and state["attempt"] < task.max_attempts:
                    next_state = "pending"
                    reason = "read_only_retry"
                else:
                    next_state = "failed"
                    reason = "lease_expired"
                db.execute("UPDATE workflow_tasks SET state=?,owner_token=NULL,lease_until=NULL,error_type=? WHERE workflow_id=? AND task_id=?",
                           (next_state, reason, workflow_id, task.task_id))
                self._event(db, workflow_id, task.task_id, "lease_reconciled", {"result": next_state, "reason": reason})
        row, spec, states = self._load(db, workflow_id)
        for task in spec.tasks:
            if states[task.task_id]["state"] == "pending" and any(
                states[dep]["state"] in _NON_SUCCESS for dep in task.depends_on):
                db.execute("UPDATE workflow_tasks SET state='blocked',error_type='dependency_not_verified' WHERE workflow_id=? AND task_id=?",
                           (workflow_id, task.task_id))
                self._event(db, workflow_id, task.task_id, "dependency_blocked", {})
        # Fixpoint: cascade failures through arbitrarily deep DAGs.
        changed = True
        while changed:
            row, spec, states = self._load(db, workflow_id)
            changed = False
            for task in spec.tasks:
                if states[task.task_id]["state"] == "pending" and any(
                    states[dep]["state"] in _NON_SUCCESS for dep in task.depends_on):
                    db.execute("UPDATE workflow_tasks SET state='blocked',error_type='dependency_not_verified' WHERE workflow_id=? AND task_id=?",
                               (workflow_id, task.task_id))
                    self._event(db, workflow_id, task.task_id, "dependency_blocked", {})
                    changed = True
        self._settle(db, workflow_id)

    def _settle(self, db: sqlite3.Connection, workflow_id: str) -> None:
        """Set final workflow state only once all task states are terminal."""
        row, spec, states = self._load(db, workflow_id)
        if row["state"] != "running" or not all(s["state"] in _FINISHED for s in states.values()):
            return
        kinds = {s["state"] for s in states.values()}
        result = ("blocked" if kinds & {"blocked", "failed", "cancelled"} else
                  "needs_real_tests" if kinds & {"mock_passed", "needs_real_tests"} else "verified_stages")
        db.execute("UPDATE workflows SET state=? WHERE workflow_id=?", (result, workflow_id))
        self._event(db, workflow_id, None, "finished", {"state": result, "task_completed": False})

    def claim(self, workflow_id: str, *, worker_kinds: set[str],
              lease_seconds: int = 120, now: float | None = None,
              worker_routes: set[str] | None = None) -> TaskLease | None:
        """Atomically claim one ready task under concurrency and dollar ceilings.

        No write task is ever retried by this dispatcher. Model-capable adapters
        must be cost-bounded by their own provider transport as well.
        """
        if not 5 <= lease_seconds <= 3600 or not worker_kinds <= _ALLOWED_KINDS:
            raise ValueError("Invalid lease or worker capabilities")
        t = time.time() if now is None else now
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._refresh(db, workflow_id, t)
            row, spec, states = self._load(db, workflow_id)
            if row["state"] != "running" or sum(s["state"] == "leased" for s in states.values()) >= spec.max_workers:
                return None
            for task in spec.tasks:
                state = states[task.task_id]
                if (task.kind not in worker_kinds or
                        (worker_routes is not None and task.kind not in worker_routes and
                         f"{task.kind}:{task.role}" not in worker_routes) or
                        state["state"] != "pending" or
                        not all(states[dep]["state"] == "verified" for dep in task.depends_on)):
                    continue
                reserve = _usd_micros(task.max_cost_usd)
                if row["reserved_micros"] + reserve > row["budget_micros"]:
                    db.execute("UPDATE workflow_tasks SET state='blocked', error_type='insufficient_budget' WHERE workflow_id=? AND task_id=?",
                               (workflow_id, task.task_id))
                    self._event(db, workflow_id, task.task_id, "budget_blocked", {})
                    self._refresh(db, workflow_id, t)
                    return None
                owner_token = secrets.token_urlsafe(24)
                generation = state["generation"] + 1
                db.execute("UPDATE workflow_tasks SET state='leased',attempt=attempt+1,generation=?,owner_token=?,lease_until=?,cost_micros=cost_micros+? WHERE workflow_id=? AND task_id=? AND state='pending'",
                           (generation, owner_token, t + lease_seconds, reserve, workflow_id, task.task_id))
                db.execute("UPDATE workflows SET reserved_micros=reserved_micros+? WHERE workflow_id=?",
                           (reserve, workflow_id))
                self._event(db, workflow_id, task.task_id, "claimed", {"attempt": state["attempt"] + 1,
                                                                       "generation": generation, "reserved_cost_micros": reserve,
                                                                       "role": task.role})
                handoffs = tuple(TaskHandoff(dep, next(x.kind for x in spec.tasks if x.task_id == dep),
                                             states[dep]["receipt_sha256"]) for dep in task.depends_on)
                return TaskLease(workflow_id, row["project_id"], Path(row["project_path"]), task,
                                 state["attempt"] + 1, generation, owner_token,
                                 task.max_cost_usd, handoffs)
            self._settle(db, workflow_id)
        return None

    def heartbeat(self, lease: TaskLease, *, lease_seconds: int = 120) -> None:
        """Renew a currently owned lease; stale generations cannot extend it."""
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("UPDATE workflow_tasks SET lease_until=? WHERE workflow_id=? AND task_id=? AND owner_token=? AND generation=? AND state='leased' AND lease_until>?",
                               (time.time() + lease_seconds, lease.workflow_id, lease.task.task_id,
                                lease.owner_token, lease.generation, time.time())).rowcount
            if count != 1:
                raise WorkflowConflict("Stale or expired heartbeat")

    def finish(self, lease: TaskLease, *, state: str, receipt: WorkerReceipt,
               now: float | None = None,
               verifier: IndependentTaskVerifier | None = None) -> None:
        """CAS-complete only if the canonical host evidence gate agrees.

        A plugin or caller cannot upgrade ``mock_passed`` to ``verified`` by
        directly invoking the journal. Custom verifier implementations may
        tighten a result but never relax the canonical safety gate.
        """
        if state not in _FINISHED or state == "cancelled":
            raise ValueError("Invalid worker completion")
        baseline = IndependentTaskVerifier(self.store,
            verifier.state_root if isinstance(verifier, IndependentTaskVerifier) else None).verify(lease, receipt)
        if state == "verified" and baseline != "verified" or (
                state in {"mock_passed", "needs_real_tests"} and baseline != state):
            raise WorkflowConflict("Unverified stage completion rejected by host gate")
        actual = _usd_micros(receipt.cost_usd)
        reserved = _usd_micros(lease.reserved_cost_usd)
        if actual > reserved:
            raise WorkflowConflict("Worker exceeded pre-reserved spending ceiling")
        t = time.time() if now is None else now
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row, spec, states = self._load(db, lease.workflow_id)
            task = states[lease.task.task_id]
            if (row["state"] != "running" or task["state"] != "leased" or
                task["owner_token"] != lease.owner_token or task["generation"] != lease.generation or
                (task["lease_until"] or 0) <= t):
                raise WorkflowConflict("Stale, expired or already finalized lease")
            evidence = {"status": receipt.status, "evidence_sha256": receipt.evidence_sha256,
                        "repair_session_id": receipt.repair_session_id,
                        "nested_run_id": receipt.nested_run_id, "cost_micros": actual}
            db.execute("UPDATE workflow_tasks SET state=?,owner_token=NULL,lease_until=NULL,receipt_sha256=?,error_type=NULL WHERE workflow_id=? AND task_id=?",
                       (state, _digest(evidence), lease.workflow_id, lease.task.task_id))
            # Reservations remain charged after calls. Unknown spending is
            # never reclaimed optimistically at workflow level.
            self._event(db, lease.workflow_id, lease.task.task_id, "finished", {"state": state,
                                                                              "receipt_sha256": _digest(evidence),
                                                                              "nested_run_id": receipt.nested_run_id or None})
            self._refresh(db, lease.workflow_id, t)

    def quarantine(self, lease: TaskLease, *, reason: str = "untrusted_result") -> None:
        """Mark an uncertain or over-budget claim terminal without retrying it."""
        if reason not in {"untrusted_result", "over_budget", "invalid_receipt"}:
            raise ValueError("Invalid quarantine reason")
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row, _, states = self._load(db, lease.workflow_id)
            state = states[lease.task.task_id]
            if (row["state"] != "running" or state["state"] != "leased" or
                state["generation"] != lease.generation or state["owner_token"] != lease.owner_token):
                raise WorkflowConflict("Stale task quarantine")
            db.execute("UPDATE workflow_tasks SET state='blocked',owner_token=NULL,lease_until=NULL,error_type=? WHERE workflow_id=? AND task_id=?",
                       (reason, lease.workflow_id, lease.task.task_id))
            self._event(db, lease.workflow_id, lease.task.task_id, "quarantined", {"reason": reason})
            self._refresh(db, lease.workflow_id, time.time())

    def fail(self, lease: TaskLease, exc: Exception, *, now: float | None = None) -> None:
        """Fail safely. Only read-only tasks may retry after a definite exception."""
        t = time.time() if now is None else now
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row, _, states = self._load(db, lease.workflow_id)
            entry = states[lease.task.task_id]
            if (row["state"] != "running" or entry["state"] != "leased" or
                    entry["owner_token"] != lease.owner_token or entry["generation"] != lease.generation or
                    (entry["lease_until"] or 0) <= t):
                raise WorkflowConflict("Stale or expired task failure")
            retry = (lease.task.kind in {"prepare", "inspect", "review"} and _usd_micros(lease.task.max_cost_usd) == 0 and
                     entry["attempt"] < lease.task.max_attempts)
            state = "pending" if retry else "blocked" if lease.task.kind == "code" else "failed"
            db.execute("UPDATE workflow_tasks SET state=?,owner_token=NULL,lease_until=NULL,error_type=? WHERE workflow_id=? AND task_id=?",
                       (state, type(exc).__name__, lease.workflow_id, lease.task.task_id))
            self._event(db, lease.workflow_id, lease.task.task_id, "failed_attempt", {"error_type": type(exc).__name__,
                                                                                       "retry_eligible": retry})
            self._refresh(db, lease.workflow_id, t)

    def reconcile(self, workflow_id: str, *, now: float | None = None) -> dict:
        """Reconcile stale tasks without running them or trusting agent output."""
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._refresh(db, workflow_id, time.time() if now is None else now)
        return self.status(workflow_id)

    def cancel(self, workflow_id: str) -> dict:
        """Revoke all task claims; running external processes are not killed.

        Operators must separately terminate and inspect any coding sandbox.
        Late completions fail the owner-token CAS check.
        """
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row, _, states = self._load(db, workflow_id)
            if row["state"] not in {"running", "cancelled"}:
                raise WorkflowConflict("Cannot cancel completed workflow")
            db.execute("UPDATE workflows SET state='cancelled' WHERE workflow_id=?", (workflow_id,))
            db.execute("UPDATE workflow_tasks SET state='cancelled',owner_token=NULL,lease_until=NULL WHERE workflow_id=? AND state IN ('pending','leased')",
                       (workflow_id,))
            self._event(db, workflow_id, None, "cancelled", {"in_flight_count": sum(
                v["state"] == "leased" for v in states.values())})
        return self.status(workflow_id)

    def status(self, workflow_id: str) -> dict:
        """Return a safe, source-free view for CLI, web and other clients."""
        with self.store._connection() as db:
            row, spec, states = self._load(db, workflow_id)
        tasks = [{"task_id": t.task_id, "kind": t.kind, "role": t.role, "depends_on": list(t.depends_on),
                  "state": states[t.task_id]["state"], "attempt": states[t.task_id]["attempt"],
                  "error_type": states[t.task_id]["error_type"],
                  "receipt_sha256": states[t.task_id]["receipt_sha256"]} for t in spec.tasks]
        return {"workflow_id": workflow_id, "project_id": row["project_id"],
                "state": row["state"], "max_workers": spec.max_workers,
                "reserved_cost_usd": row["reserved_micros"] / 1e6,
                "budget_usd": row["budget_micros"] / 1e6,
                "tasks": tasks, "task_completed": False}

    def attest(self, workflow_id: str, *, state_root: Path) -> dict:
        """Re-check completed code-review evidence *at inspection time*.

        The original append-only workflow result is not silently rewritten.
        This is a separate, read-only current-integrity check; it detects
        patch tampering or source HEAD changes after earlier verification.
        It does not certify the user's broad task or promise that files will
        remain unchanged after the attestation completes.
        """
        from .code_review import CodeReviewService, ReviewBlocked
        with self.store._connection() as db:
            row, spec, states = self._load(db, workflow_id)
        reviews = []
        for task in spec.tasks:
            if task.kind != "review":
                continue
            result = {"task_id": task.task_id, "status": "not_verified"}
            if states[task.task_id]["state"] == "verified":
                predecessor = task.depends_on[0]
                try:
                    evidence = CodeReviewService(self.store, state_root).examine(
                        workflow_id=workflow_id, project_id=row["project_id"],
                        project_path=Path(row["project_path"]), code_task_id=predecessor,
                        expected_receipt_sha256=states[predecessor]["receipt_sha256"])
                    receipt = WorkerReceipt("reviewed", evidence_sha256=evidence.sha256)
                    receipt_digest = _digest({"status": receipt.status,
                        "evidence_sha256": receipt.evidence_sha256,
                        "repair_session_id": receipt.repair_session_id,
                        "nested_run_id": receipt.nested_run_id, "cost_micros": 0})
                    result["status"] = ("current" if receipt_digest ==
                        states[task.task_id]["receipt_sha256"] else "stale")
                except (ReviewBlocked, OSError, KeyError, ValueError):
                    result["status"] = "stale"
            reviews.append(result)
        overall = ("no_reviews" if not reviews else
                   "current" if all(r["status"] == "current" for r in reviews) else
                   "stale" if any(r["status"] == "stale" for r in reviews) else "not_verified")
        return {"workflow_id": workflow_id, "project_id": row["project_id"],
                "review_attestation": overall, "reviews": reviews,
                "task_completed": False}

    def events(self, workflow_id: str) -> list[dict]:
        """Read only bounded, metadata-only workflow transition evidence."""
        with self.store._connection() as db:
            self._load(db, workflow_id)
            rows = db.execute("SELECT id,task_id,event,metadata_json,created_at FROM workflow_events WHERE workflow_id=? ORDER BY id",
                              (workflow_id,)).fetchall()
        return [{"id": r["id"], "task_id": r["task_id"], "event": r["event"],
                 "metadata": json.loads(r["metadata_json"]), "created_at": r["created_at"]} for r in rows]


class WorkflowSupervisor:
    """Bounded concurrent dispatcher with injected workers and independent checks.

    No implicit network, shell, Docker or write permissions. Two local
    supervisors can poll concurrently; SQLite serializes claims. Worker-facing
    leases are opaque and finalization is owner/generation checked. Thread
    concurrency is local-only and does not imply distributed worker support.
    """

    def __init__(self, journal: WorkflowJournal,
                 workers: Mapping[str, WorkflowWorker] | None = None,
                 verifier: IndependentTaskVerifier | None = None):
        self.journal = journal
        self.workers = dict(workers or {"prepare": PreparationWorker(journal.store),
                                        "mock": ScriptedWorkflowWorker()})
        if any((k not in _ALLOWED_KINDS and not (":" in k and k.split(":", 1)[0] in _ALLOWED_KINDS
                and k.split(":", 1)[1] in _ROLES)) or not isinstance(v, WorkflowWorker)
                for k, v in self.workers.items()):
            raise TypeError("Invalid workflow worker registration")
        self.verifier = verifier or IndependentTaskVerifier(journal.store)

    def run(self, workflow_id: str, *, lease_seconds: int = 120,
            max_wall_seconds: int = 120, max_dispatch: int = 100,
            stop_event: threading.Event | None = None) -> dict:
        """Schedule eligible tasks and wait for local workers within soft limits.

        ``max_wall_seconds`` is a best-effort dispatch limit, not a hard process
        or sandbox timeout. Per-worker provider and test timeouts are required.
        """
        if not 5 <= max_wall_seconds <= 3600 or not 1 <= max_dispatch <= 200:
            raise ValueError("Invalid supervisor limits")
        begin = time.monotonic()
        stop = stop_event or threading.Event()
        max_workers = self.journal.status(workflow_id)["max_workers"]
        pending = {}
        scheduled = 0

        def work(lease: TaskLease) -> tuple[str, WorkerReceipt]:
            route = f"{lease.task.kind}:{lease.task.role}"
            selected = self.workers.get(route) or self.workers[lease.task.kind]
            receipt = selected.execute(lease.assignment())
            if not isinstance(receipt, WorkerReceipt):
                raise TypeError("Worker did not return a typed receipt")
            outcome = self.verifier.verify(lease, receipt)
            return outcome, receipt

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while True:
                while (not stop.is_set() and len(pending) < max_workers and scheduled < max_dispatch and
                       time.monotonic() - begin < max_wall_seconds):
                    lease = self.journal.claim(workflow_id,
                        worker_kinds={k.split(":", 1)[0] for k in self.workers},
                        worker_routes=set(self.workers), lease_seconds=lease_seconds)
                    if lease is None:
                        break
                    pending[pool.submit(work, lease)] = lease
                    scheduled += 1
                if not pending:
                    break
                # Keep host-owned task leases alive while bounded worker calls
                # are executing. A process crash stops these renewals; the
                # persisted journal decides which stages can safely retry.
                done, _ = wait(pending, timeout=min(2.0, lease_seconds / 3),
                               return_when=FIRST_COMPLETED)
                for future in done:
                    lease = pending.pop(future)
                    try:
                        state, receipt = future.result()
                    except Exception as exc:
                        try:
                            self.journal.fail(lease, exc)
                        except WorkflowConflict:
                            pass  # expired/cancelled lease: never overwrite newer owner
                    else:
                        try:
                            self.journal.finish(lease, state=state, receipt=receipt, verifier=self.verifier)
                        except (WorkflowConflict, ValueError) as exc:
                            # Over-budget or malformed worker receipts are not
                            # normal failures and must never enter retry logic.
                            try:
                                self.journal.quarantine(lease, reason="over_budget" if receipt.cost_usd > lease.reserved_cost_usd else
                                                        "invalid_receipt")
                            except WorkflowConflict:
                                pass  # An expired or cancelled lease owns nothing.
                for outstanding_lease in pending.values():
                    try:
                        self.journal.heartbeat(outstanding_lease, lease_seconds=lease_seconds)
                    except WorkflowConflict:
                        # A cancelled/replaced claim cannot be renewed; its
                        # late result will also fail owner/generation fencing.
                        pass

        return self.journal.reconcile(workflow_id)
