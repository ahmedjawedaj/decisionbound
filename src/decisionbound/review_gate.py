"""Local human review of code artifacts, with tamper-evident export authorization.

An approval authorizes **exporting a patch file only**. It cannot mark a task
completed, approve execution, merge, commit, push, or change a source checkout.
The service uses the Sprint 04 artifact ledger and Sprint 07 workflow journal,
so there is no second source of truth for test results or code provenance.

The review decision is a local OS-user action, NOT a claim of authenticated
human identity or remote enterprise authorization. Subclasses may only add
vetoes; the host's canonical integrity checks remain mandatory.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from .code_artifacts import export_coding_patch
from .code_journal import SQLiteRepairJournal
from .code_patches import PatchValidator, UnsafePatch
from .code_workspace import GitWorktreeManager, WorkspaceError
from .identity import load_project
from .storage import SQLiteRepository
from .workflows import WorkflowJournal, _code_idempotency

_WF = re.compile(r"^wf_[a-f0-9]{32}$")


class ReviewUnavailable(ValueError):
    """The workflow has no safe, unambiguous coding artifact to review."""


class ReviewConflict(ValueError):
    """The review is stale, missing consent, or conflicts with another client."""


@dataclass(frozen=True)
class ReviewSnapshot:
    """A read-time view of the exact immutable artifact and source state.

    ``fingerprint`` includes workflow, project, commit, contract, run, patch,
    and current verification status. This is a short-lived authorization input,
    not a promise that the filesystem will remain unchanged afterward.
    """

    workflow_id: str
    project_id: str
    run_id: str
    patch_sha256: str
    fingerprint: str
    source_commit: str
    paths: tuple[str, ...]
    added_lines: int
    removed_lines: int
    size_bytes: int
    workflow_state: str
    code_state: str
    review_attestation: str
    contract_verified: bool
    export_requires_unverified_acknowledgment: bool
    task_completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return metadata without code text, secrets, or local filesystem paths."""
        return {**asdict(self), "paths": list(self.paths)}


class ExportReviewPolicy(ABC):
    """Optional veto-only policy for organizations extending export review.

    Override ``allow`` to add more restrictions. It cannot grant export rights,
    weaken stale-evidence checks or bypass the required local approval ledger.
    """

    @abstractmethod
    def allow(self, snapshot: ReviewSnapshot) -> bool:
        """Return false to veto an otherwise valid export."""


class LocalReviewPolicy(ExportReviewPolicy):
    """Accept exports only after all mandatory host-side gates have passed."""

    def allow(self, snapshot: ReviewSnapshot) -> bool:
        """Do not add an additional veto on top of host verification."""
        return True


class ReviewGate:
    """Snapshot, approve/reject, revoke, and export one coding patch.

    Args:
        repository: The SQLite store owning the workflow and repair journals.
        state_root: Private state directory, separate from the Git checkout.
        policy: Additional veto-only policy; never used to grant permission.

    The caller must already have local OS-user authorization. Local HTTP
    access additionally requires the pairing token. MCP only exposes review
    metadata; it cannot approve or export patches.
    """

    def __init__(self, repository: SQLiteRepository, state_root: Path,
                 policy: ExportReviewPolicy | None = None):
        if not isinstance(repository, SQLiteRepository):
            raise TypeError("ReviewGate requires a local SQLite repository")
        if policy is not None and not isinstance(policy, ExportReviewPolicy):
            raise TypeError("Review policy must implement the veto-only interface")
        if state_root.is_symlink() or not state_root.is_dir():
            raise ReviewUnavailable("Private review state must be a real directory")
        self.repository = repository
        self.state_root = state_root.resolve(strict=True)
        self.policy = policy or LocalReviewPolicy()
        with repository._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS local_export_reviews (
                    workflow_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    acknowledge_unverified INTEGER NOT NULL,
                    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
                );
                CREATE TABLE IF NOT EXISTS local_export_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
                );
            """)

    def _source(self, workflow_id: str) -> tuple[dict, Path, str, dict, dict]:
        """Resolve exactly one completed coding attempt from its durable ledger."""
        if not _WF.fullmatch(workflow_id):
            raise ReviewUnavailable("Malformed workflow ID")
        journal = WorkflowJournal(self.repository)
        status = journal.status(workflow_id)
        with self.repository._connection() as db:
            row = db.execute("SELECT project_path,project_id FROM workflows WHERE workflow_id=?",
                             (workflow_id,)).fetchone()
        if row is None:
            raise ReviewUnavailable("Unknown workflow")
        project_path = Path(row["project_path"])
        try:
            project = load_project(project_path, self.repository)
            root, commit = GitWorktreeManager.check_source(project_path)
        except (ValueError, PermissionError, FileNotFoundError, WorkspaceError, OSError) as exc:
            raise ReviewUnavailable("Original project is missing or not clean") from exc
        if project.project_id != row["project_id"] or status["project_id"] != project.project_id:
            raise ReviewUnavailable("Project identity mismatch")
        code = [task for task in status["tasks"] if task["kind"] == "code"]
        if len(code) != 1 or code[0]["state"] in {"pending", "leased", "cancelled"}:
            raise ReviewUnavailable("Workflow has no terminal, unambiguous coding task")
        key = _code_idempotency(workflow_id, code[0]["task_id"])
        with self.repository._connection() as db:
            repair = db.execute("""SELECT id,project_id FROM repair_sessions
                                   WHERE idempotency_key=?""", (key,)).fetchone()
        if repair is None or repair["project_id"] != project.project_id:
            raise ReviewUnavailable("Coding session not bound to this project")
        state = SQLiteRepairJournal(self.repository, self.state_root).get(repair["id"])
        if not state.attempts or state.attempts[-1].status == "in_flight":
            raise ReviewUnavailable("Attempt is incomplete or unsafe to review")
        attempt = state.attempts[-1]
        events = self.repository.events(attempt.run_id)
        def unique(name: str) -> dict:
            matches = [e["payload"] for e in events if e["event"] == name]
            if len(matches) != 1:
                raise ReviewUnavailable("Incomplete or ambiguous " + name + " evidence")
            return matches[0]
        started = unique("code_started")
        worktree = unique("code_worktree_created")
        patch = unique("code_patch_applied")
        finished = unique("code_finished")
        cleaned = unique("code_workspace_cleaned")
        if (started.get("project_id") != project.project_id or
                started.get("contract_sha256") != state.contract_sha256 or
                finished.get("contract_sha256") != state.contract_sha256 or
                worktree.get("base_commit") != commit or cleaned.get("ok") is not True or
                not started.get("write_approved") or
                any(e["event"] == "code_failed" for e in events)):
            raise ReviewUnavailable("Artifact provenance or cleanup is inconsistent")
        return status, root, commit, patch, {"attempt": attempt, "finished": finished,
                                              "contract_sha256": state.contract_sha256}

    def _patch_bytes(self, run_id: str, receipt: dict) -> bytes:
        """Read a regular artifact without following a symlink or exceeding limits."""
        if not re.fullmatch(r"code_[0-9a-f]{32}", run_id):
            raise ReviewUnavailable("Malformed code run ID")
        directory = self.state_root / "code-artifacts"
        if directory.is_symlink() or not directory.is_dir():
            raise ReviewUnavailable("Patch artifact directory unavailable")
        source = directory / (run_id + ".patch")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(source, flags)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 250_000:
                    raise ReviewUnavailable("Patch artifact is not a bounded regular file")
                data = handle.read(250_001)
        except OSError as exc:
            raise ReviewUnavailable("Patch artifact cannot be opened safely") from exc
        if (len(data) > 250_000 or sha256(data).hexdigest() != receipt.get("sha256") or
                len(data) != receipt.get("size_bytes")):
            raise ReviewUnavailable("Patch artifact does not match the host receipt")
        return data

    def snapshot(self, workflow_id: str, *, include_diff: bool = False) -> dict[str, Any]:
        """Inspect a pending patch, optionally returning its local-only raw diff.

        The diff contains source code and must never be persisted to event
        logs or exposed as an MCP tool response. HTTP diff access requires an
        authenticated local-user token and an explicit ``include_diff`` query.
        """
        status, root, commit, receipt, evidence = self._source(workflow_id)
        run_id = evidence["attempt"].run_id
        data = self._patch_bytes(run_id, receipt)
        try:
            inspection = PatchValidator().inspect(data.decode("utf-8"), root)
            PatchValidator().check_git_modes(root, inspection.paths)
        except (UnicodeError, UnsafePatch, OSError, ValueError) as exc:
            raise ReviewUnavailable("Patch failed independent scope validation") from exc
        if (receipt.get("paths") != list(inspection.paths) or
                inspection.sha256 != receipt.get("sha256")):
            raise ReviewUnavailable("Changed-file receipt does not match patch")
        attestation = WorkflowJournal(self.repository).attest(workflow_id, state_root=self.state_root)
        code_state = next(t["state"] for t in status["tasks"] if t["kind"] == "code")
        verified = (code_state == "verified" and
                    attestation["review_attestation"] == "current" and
                    evidence["finished"].get("acceptance_verified") is True)
        canonical = {
            "workflow_id": workflow_id, "project_id": status["project_id"],
            "run_id": run_id, "patch_sha256": inspection.sha256,
            "source_commit": commit, "contract_sha256": evidence["contract_sha256"],
            "workflow_state": status["state"], "code_state": code_state,
            "review_attestation": attestation["review_attestation"],
            "contract_verified": verified,
        }
        digest = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        snapshot = ReviewSnapshot(workflow_id, status["project_id"], run_id,
                                  inspection.sha256, digest, commit, inspection.paths,
                                  inspection.added_lines, inspection.removed_lines,
                                  inspection.size_bytes, status["state"], code_state,
                                  attestation["review_attestation"], verified,
                                  not verified)
        result = snapshot.to_dict()
        if include_diff:
            result["diff"] = data.decode("utf-8")
        result["approval"] = self.decision(workflow_id)
        return result

    def decision(self, workflow_id: str) -> dict[str, Any]:
        """Return the latest local approval revision; never return code text."""
        if not _WF.fullmatch(workflow_id):
            raise ReviewUnavailable("Malformed workflow ID")
        with self.repository._connection() as db:
            row = db.execute("SELECT * FROM local_export_reviews WHERE workflow_id=?",
                             (workflow_id,)).fetchone()
        if row is None:
            return {"workflow_id": workflow_id, "revision": 0,
                    "decision": "pending", "fingerprint": None,
                    "acknowledge_unverified": False}
        return {"workflow_id": workflow_id, "revision": row["revision"],
                "decision": row["decision"], "fingerprint": row["fingerprint"],
                "acknowledge_unverified": bool(row["acknowledge_unverified"])}

    def decide(self, workflow_id: str, *, expected_fingerprint: str,
               expected_revision: int, action: str,
               acknowledge_unverified: bool = False) -> dict[str, Any]:
        """Record a local approve/reject/revoke action with optimistic locking.

        Approval is not a claim of human identity or task correctness. A
        rejected fingerprint cannot be re-approved; new evidence is required.
        Revocation needs the expected current revision but works even when a
        patch has subsequently become stale.
        """
        if action not in {"approve_export", "reject", "revoke"}:
            raise ReviewConflict("Unknown review action")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ReviewConflict("Review revision must be nonnegative")
        if not isinstance(expected_fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint):
            raise ReviewConflict("Expected fingerprint must be SHA-256")
        if action == "revoke":
            current = self.decision(workflow_id)
            if (current["decision"] != "approve_export" or
                    current["revision"] != expected_revision or
                    current["fingerprint"] != expected_fingerprint):
                raise ReviewConflict("No matching approved revision to revoke")
            project_id = None
        else:
            snapshot = self.snapshot(workflow_id)
            if snapshot["fingerprint"] != expected_fingerprint:
                raise ReviewConflict("Review evidence changed since inspection")
            if (action == "approve_export" and snapshot["export_requires_unverified_acknowledgment"]
                    and acknowledge_unverified is not True):
                raise ReviewConflict("Explicit acknowledgement required for unverified code")
            project_id = snapshot["project_id"]
        with self.repository._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM local_export_reviews WHERE workflow_id=?", (workflow_id,)).fetchone()
            revision = 0 if row is None else row["revision"]
            if revision != expected_revision:
                raise ReviewConflict("Stale review revision: another client made a decision")
            if action == "revoke":
                if row is None or row["decision"] != "approve_export" or row["fingerprint"] != expected_fingerprint:
                    raise ReviewConflict("Approval is no longer active")
                project_id = row["project_id"]
            elif row is not None and row["decision"] == "reject" and row["fingerprint"] == expected_fingerprint:
                raise ReviewConflict("Rejected evidence requires a new patch or workflow")
            if row is None:
                db.execute("""INSERT INTO local_export_reviews
                    (workflow_id,project_id,revision,fingerprint,decision,acknowledge_unverified)
                    VALUES(?,?,?,?,?,?)""",
                    (workflow_id,project_id,1,expected_fingerprint,action,int(bool(acknowledge_unverified))))
            else:
                db.execute("""UPDATE local_export_reviews SET revision=?,fingerprint=?,decision=?,
                    acknowledge_unverified=?,changed_at=datetime('now') WHERE workflow_id=? AND revision=?""",
                    (revision+1,expected_fingerprint,action,
                     int(bool(acknowledge_unverified) if action == "approve_export" else False),
                     workflow_id,revision))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ReviewConflict("Concurrent review modification")
            db.execute("INSERT INTO local_export_events(workflow_id,event,fingerprint) VALUES (?,?,?)",
                       (workflow_id, action, expected_fingerprint))
            # Publish a metadata-only change in the canonical workflow stream.
            # This happens in the SAME transaction as the approval CAS update,
            # so an editor reconnect cannot miss a committed review revision.
            WorkflowJournal._event(db, workflow_id, None, "review_decision",
                                   {"decision": action, "revision": revision + 1})
        return self.decision(workflow_id)

    def history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Read metadata-only local review decisions and export event history."""
        if not _WF.fullmatch(workflow_id):
            raise ReviewUnavailable("Malformed workflow ID")
        # Ensure the workflow exists before returning an empty event list.
        WorkflowJournal(self.repository).status(workflow_id)
        with self.repository._connection() as db:
            rows = db.execute("""SELECT id,event,fingerprint,recorded_at
                                  FROM local_export_events WHERE workflow_id=? ORDER BY id""",
                              (workflow_id,)).fetchall()
        return [dict(row) for row in rows]

    def export(self, workflow_id: str, *, destination: Path,
               expected_fingerprint: str, expected_revision: int) -> dict[str, Any]:
        """Export only an explicitly approved, still-current code patch.

        This method does not apply or merge code. It refuses destinations inside
        the source checkout and delegates exclusive-file creation and checksum
        validation to Sprint 04's patch-export component.
        """
        review = self.decision(workflow_id)
        if (review["revision"] != expected_revision or review["decision"] != "approve_export" or
                review["fingerprint"] != expected_fingerprint):
            raise ReviewConflict("Review approval is missing, revoked, or out of date")
        snapshot = self.snapshot(workflow_id)
        if snapshot["fingerprint"] != expected_fingerprint:
            raise ReviewConflict("Patch or project evidence changed after approval")
        if not self.policy.allow(ReviewSnapshot(**{
            k: tuple(snapshot[k]) if k == "paths" else snapshot[k]
            for k in ReviewSnapshot.__dataclass_fields__ if k in snapshot
        })):
            raise ReviewConflict("Organization policy vetoed export")
        with self.repository._connection() as db:
            project = db.execute("SELECT project_path FROM workflows WHERE workflow_id=?",
                                 (workflow_id,)).fetchone()
        root = Path(project["project_path"]).resolve(strict=True)
        parent = destination.parent.resolve(strict=True)
        if parent == root or root in parent.parents:
            raise ReviewConflict("Export destination cannot be inside the source repository")
        # Hold the one local SQLite writer lock for the short (<= 250KB) file
        # export so a simultaneous revocation cannot win between the approval
        # check and creation of the export. There is deliberately no automatic
        # retry of a crash between file creation and the audit insert.
        with self.repository._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT decision,revision,fingerprint,acknowledge_unverified FROM local_export_reviews WHERE workflow_id=?",
                             (workflow_id,)).fetchone()
            if (row is None or row["decision"] != "approve_export" or
                    row["revision"] != expected_revision or row["fingerprint"] != expected_fingerprint):
                raise ReviewConflict("Review was revoked before export")
            output = export_coding_patch(repository=self.repository,
                                         state_root=self.state_root, run_id=snapshot["run_id"],
                                         destination=destination,
                                         acknowledge_unverified=bool(row["acknowledge_unverified"]))
            db.execute("INSERT INTO local_export_events(workflow_id,event,fingerprint) VALUES(?,?,?)",
                       (workflow_id, "exported", expected_fingerprint))
            WorkflowJournal._event(db, workflow_id, None, "review_exported",
                                   {"revision": expected_revision, "task_completed": False})
        return {**output, "workflow_id": workflow_id, "review_revision": expected_revision,
                "review_fingerprint": expected_fingerprint, "task_completed": False,
                "auto_merged": False}
