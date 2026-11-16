"""Read-only, cursor-based local client synchronization for DecisionBound.

The CLI, browser, and editor read *one* SQLite-backed workflow ledger.  This
module does not create a second session store or trust client-supplied results.
All queries are project-scoped, bounded, parameterized and metadata-only.

A session currently represents a durable workflow (``wf_*``). Future protocol
versions may include other run types, without pretending that legacy runs are
synchronized automatically. Event cursors are SQLite's global, monotonically
increasing workflow event IDs; clients can reconnect using ``after``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .storage import SQLiteRepository

PROJECT_ID = re.compile(r"^prj_[0-9a-f]{32}$")
SESSION_ID = re.compile(r"^wf_[0-9a-f]{32}$")
MAX_PAGE_SIZE = 100
PROTOCOL_VERSION = 1


class LocalSyncError(ValueError):
    """Invalid project, session, cursor, or protocol parameter."""


def _validated_int(value: int, *, name: str, minimum: int, maximum: int) -> int:
    """Refuse booleans, negative cursors, and unbounded result requests."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise LocalSyncError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


class LocalClientSync:
    """Provide bounded, project-scoped views of the canonical workflow journal.

    Args:
        repository: The same SQLiteRepository used by all other clients.

    Override contract:
        Read methods may be replaced for another storage backend, but must
        enforce both bearer-token checks at the transport layer *and* project
        scoping in the data layer. Never accept a caller's project ID alone as
        evidence of identity; local use is gated by an explicit pairing token.

    Security invariants:
        This class never returns model prompts, code diffs, local file paths,
        authentication tokens, lease credentials, or worker-provided completion
        assertions. It does not modify the workflow ledger.
    """

    def __init__(self, repository: SQLiteRepository):
        self.repository = repository
        # Ensure a pre-existing database has the ledger tables before querying.
        from .workflows import WorkflowJournal
        WorkflowJournal(repository)

    def _project(self, project_id: str) -> None:
        """Require a known, correctly formed local project identifier."""
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise LocalSyncError("Invalid project ID")
        if self.repository.get_project(project_id) is None:
            raise LocalSyncError("Unknown project ID")

    @staticmethod
    def capabilities() -> dict[str, Any]:
        """Advertise the implemented contract, not future experimental APIs."""
        return {
            "protocol": "decisionbound-local-client",
            "version": PROTOCOL_VERSION,
            "transport": "authenticated-loopback-http",
            "event_delivery": "cursor-polling",
            "session_kinds": ["workflow"],
            "editor_permissions": ["list", "inspect", "events", "review_metadata"],
            "review_mutations": "web-or-cli-with-explicit-confirmation",
            "auto_merge": False,
            "task_completion_certified": False,
        }

    def list_sessions(self, project_id: str, *, limit: int = 25,
                      before: int | None = None) -> dict[str, Any]:
        """Page workflow sessions by immutable creation event sequence.

        ``before`` is an exclusive creation-event ID; it remains stable when
        other clients append events to existing workflows. Session IDs and
        workflow states are canonical, not client caches.
        """
        self._project(project_id)
        _validated_int(limit, name="limit", minimum=1, maximum=MAX_PAGE_SIZE)
        if before is not None:
            _validated_int(before, name="before", minimum=1, maximum=2**63 - 1)
        with self.repository._connection() as db:
            rows = db.execute("""
                SELECT w.workflow_id, w.state, w.created_at,
                    (SELECT MIN(id) FROM workflow_events
                     WHERE workflow_id=w.workflow_id) AS created_seq,
                    (SELECT MAX(id) FROM workflow_events
                     WHERE workflow_id=w.workflow_id) AS last_seq,
                    (SELECT COUNT(*) FROM workflow_tasks
                     WHERE workflow_id=w.workflow_id) AS task_count
                FROM workflows AS w
                WHERE w.project_id=? AND
                    (? IS NULL OR (SELECT MIN(id) FROM workflow_events
                     WHERE workflow_id=w.workflow_id) < ?)
                ORDER BY created_seq DESC, w.workflow_id DESC LIMIT ?
            """, (project_id, before, before, limit + 1)).fetchall()
        more = len(rows) > limit
        items = [{"session_id": row["workflow_id"], "kind": "workflow",
                  "project_id": project_id, "state": row["state"],
                  "created_at": row["created_at"], "created_seq": row["created_seq"],
                  "last_event_id": row["last_seq"], "task_count": row["task_count"],
                  "task_completed": False} for row in rows[:limit]]
        return {"project_id": project_id, "sessions": items,
                "next_before": items[-1]["created_seq"] if more and items else None}

    def snapshot(self, project_id: str, session_id: str) -> dict[str, Any]:
        """Return the verified journal's safe workflow status for one project."""
        self._project(project_id)
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise LocalSyncError("Invalid session ID")
        from .workflows import WorkflowJournal
        try:
            result = WorkflowJournal(self.repository).status(session_id)
        except KeyError as exc:
            raise LocalSyncError("Unknown session") from exc
        if result["project_id"] != project_id:
            raise LocalSyncError("Unknown session")
        return result

    def review_state(self, project_id: str, session_id: str) -> dict[str, Any]:
        """Read review approval and current integrity without exposing patch text.

        An unavailable/stale patch is represented as unavailable metadata with
        its *historical* decision still visible. Never suggest that an old
        approval is presently exportable without a fresh evidence snapshot.
        """
        self.snapshot(project_id, session_id)
        from .review_gate import ReviewGate, ReviewUnavailable
        from .settings import state_dir
        gate = ReviewGate(self.repository, state_dir())
        approval = gate.decision(session_id)
        try:
            evidence = gate.snapshot(session_id, include_diff=False)
            evidence.pop("diff", None)
            return {"session_id": session_id, "project_id": project_id,
                    "available": True, "approval": approval, "evidence": evidence,
                    "task_completed": False}
        except (ReviewUnavailable, OSError, ValueError):
            return {"session_id": session_id, "project_id": project_id,
                    "available": False, "approval": approval, "evidence": None,
                    "task_completed": False}

    def session_events(self, project_id: str, session_id: str, *, after: int = 0,
                       limit: int = 50) -> dict[str, Any]:
        """Read one session's events after an exclusive durable event cursor."""
        self.snapshot(project_id, session_id)
        _validated_int(after, name="after", minimum=0, maximum=2**63 - 1)
        _validated_int(limit, name="limit", minimum=1, maximum=MAX_PAGE_SIZE)
        with self.repository._connection() as db:
            rows = db.execute("""SELECT id,task_id,event,metadata_json,created_at
                FROM workflow_events WHERE workflow_id=? AND id>?
                ORDER BY id LIMIT ?""", (session_id, after, limit + 1)).fetchall()
        return self._page(rows, after=after, limit=limit, project_id=project_id,
                          session_id=session_id)

    def changes(self, project_id: str, *, after: int = 0,
                limit: int = 50) -> dict[str, Any]:
        """Read one project's global change feed for safe reconnect/resume.

        The cursor is global across *all* projects. Skipped IDs are normal:
        they may belong to other projects. Clients must use ``next_after``
        rather than assuming consecutive event IDs.
        """
        self._project(project_id)
        _validated_int(after, name="after", minimum=0, maximum=2**63 - 1)
        _validated_int(limit, name="limit", minimum=1, maximum=MAX_PAGE_SIZE)
        with self.repository._connection() as db:
            rows = db.execute("""SELECT e.id,e.workflow_id,e.task_id,e.event,
                    e.metadata_json,e.created_at FROM workflow_events AS e
                JOIN workflows AS w ON w.workflow_id=e.workflow_id
                WHERE w.project_id=? AND e.id>? ORDER BY e.id LIMIT ?""",
                (project_id, after, limit + 1)).fetchall()
        return self._page(rows, after=after, limit=limit, project_id=project_id)

    @staticmethod
    def _page(rows: list, *, after: int, limit: int, project_id: str,
              session_id: str | None = None) -> dict[str, Any]:
        """Produce a bounded page with one consistent exclusive next cursor."""
        more = len(rows) > limit
        events = []
        for row in rows[:limit]:
            events.append({"id": row["id"], "session_id": session_id or row["workflow_id"],
                           "task_id": row["task_id"], "event": row["event"],
                           "metadata": json.loads(row["metadata_json"]),
                           "created_at": row["created_at"]})
        return {"project_id": project_id, "events": events,
                "next_after": events[-1]["id"] if events else after,
                "has_more": more}
