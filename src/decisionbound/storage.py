"""Local transactional SQLite implementation of the repository port.

This adapter is intended for a single-user runtime. It never attempts to
implement distributed leases or multi-tenant authorization. All updates are
parameterized and optimistic concurrency is enforced for decisions/profiles.
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from .models import Decision, Project
from .ports import DecisionRepository

SCHEMA_VERSION = 2

# Audited minimum columns for core tables; downstream lazy extension tables
# retain their own migration/version contracts. Never treat table names alone
# as proof that a local database can be safely opened or restored.
SCHEMA_REQUIRED_COLUMNS = {
    "projects": frozenset({"id", "payload"}),
    "decisions": frozenset({"id", "source_project_id", "revision", "payload"}),
    "profiles": frozenset({"name", "revision", "payload"}),
    "settings": frozenset({"key", "value"}),
    "run_events": frozenset({"id", "run_id", "event", "payload", "created_at"}),
    "repair_sessions": frozenset({"id", "idempotency_key", "request_sha256", "project_id", "contract_sha256", "max_attempts", "status", "last_feedback", "cost_usd", "created_at"}),
    "repair_attempts": frozenset({"session_id", "number", "run_id", "status", "summary", "reserved_cost_usd"}),
}


class _FairLock:
    """Reentrant lock that admits waiting threads in arrival order.

    SQLite's busy handler polls with backoff and is not fair: when several
    threads write through short connections, one of them can keep losing the
    race and exceed the busy timeout even though the database makes steady
    progress. Queueing threads of one process here leaves SQLite to arbitrate
    only between processes. A holder that never releases cannot hang a waiter
    forever: the wait ends with the same error SQLite would raise.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._owner: int | None = None
        self._depth = 0
        self._queue: deque[object] = deque()

    def acquire(self, timeout: float) -> None:
        me = threading.get_ident()
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._owner == me:
                self._depth += 1
                return
            ticket = object()
            self._queue.append(ticket)
            try:
                while self._owner is not None or self._queue[0] is not ticket:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise sqlite3.OperationalError("database is locked")
                    self._condition.wait(remaining)
            except BaseException:
                self._queue.remove(ticket)
                self._condition.notify_all()
                raise
            self._queue.popleft()
            self._owner = me
            self._depth = 1

    def release(self) -> None:
        with self._condition:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()


_LOCKS: dict[str, _FairLock] = {}
_LOCKS_GUARD = threading.Lock()


def _fair_lock_for(path: Path) -> _FairLock:
    """Return the one queue shared by every repository object for a database file."""
    try:
        key = str(path.resolve())
    except OSError:
        key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, _FairLock())


class RevisionConflict(ValueError):
    """Raised when an update is based on an obsolete entity revision."""


class SQLiteRepository(DecisionRepository):
    """Persist DecisionBound state in a user-owned SQLite database.

    Args:
        path: Database path. Parent directories are created with restrictive
            permissions when possible. Symlinked database targets are rejected.

    Override guidance:
        Use :class:`DecisionRepository` for a PostgreSQL adapter. Extending
        this class directly is supported for local experimentation, but schema
        migrations and isolation guarantees are private implementation details.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if self.path.is_symlink():
            raise ValueError("Refusing a symlinked database file")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Database path must be a regular file")
        self._initialize()
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @classmethod
    def _upgrade_temporary_snapshot(cls, path: Path) -> None:
        """Upgrade only a caller-controlled temporary copy, never user state.

        This internal method intentionally bypasses public construction so
        ordinary SDK users cannot accidentally opt into in-place migration.
        The migration service must first create and own the snapshot path.
        """
        temporary = cls.__new__(cls)
        temporary.path = path
        temporary._initialize(allow_legacy_upgrade=True)

    #: Seconds SQLite waits for a lock held by another process or connection.
    busy_timeout_seconds: float = 10.0
    #: A thread queued behind others in this process is making steady progress,
    #: so it waits this many times longer before giving up. The cap only ends a
    #: wait behind a holder that never releases.
    queue_patience: float = 3.0

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        queue = _fair_lock_for(self.path)
        queue.acquire(self.busy_timeout_seconds * self.queue_patience)
        try:
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_seconds)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_seconds * 1000)}")
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            queue.release()

    def _initialize(self, *, allow_legacy_upgrade: bool = False) -> None:
        """Open the current schema without silently upgrading existing user data.

        The only supported v1 upgrade path uses :func:`state_migration.migrate_database`
        to create an independently validated, never-overwritten copy.  Its private
        ``allow_legacy_upgrade`` switch is for that isolated copy, not for the
        user-facing runtime. Check version *before* changing the journal mode.
        """
        with self._connection() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported database schema: {version}")
            if version == 1 and not allow_legacy_upgrade:
                raise RuntimeError("Legacy database schema v1: use 'dbound state migrate' to prepare a separate upgraded copy")
            if version == 0:
                existing = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()
                if existing is not None:
                    raise RuntimeError("Unversioned database contains existing tables; refusing adoption")
            if version == 1 and allow_legacy_upgrade:
                # Only patch known, structurally compatible v1 tables, on an
                # isolated snapshot. Never guess at an incompatible schema.
                expected = {
                    "projects": {"id", "payload"},
                    "decisions": {"id", "source_project_id", "revision", "payload"},
                    "profiles": {"name", "revision", "payload"},
                    "settings": {"key", "value"},
                    "run_events": {"id", "run_id", "event", "payload"},
                }
                for table, minimum in expected.items():
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                    if columns and not minimum.issubset(columns):
                        raise RuntimeError(f"Incompatible legacy table: {table}")
                event_columns = {r[1] for r in db.execute('PRAGMA table_info("run_events")')}
                if event_columns and "created_at" not in event_columns:
                    # Original timestamps were not recorded. Keep an explicit
                    # sentinel rather than inventing historical event times.
                    db.execute("ALTER TABLE run_events ADD COLUMN created_at TEXT NOT NULL DEFAULT 'unknown'")
            if version == SCHEMA_VERSION:
                for table, required in SCHEMA_REQUIRED_COLUMNS.items():
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                    if not required.issubset(columns):
                        raise RuntimeError(f"Current schema has missing or incompatible columns: {table}")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, source_project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_source ON decisions(source_project_id);
                CREATE TABLE IF NOT EXISTS profiles (
                    name TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    event TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run_id_id ON run_events(run_id, id);
                CREATE TABLE IF NOT EXISTS repair_sessions (
                    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL, project_id TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL, max_attempts INTEGER NOT NULL,
                    status TEXT NOT NULL, last_feedback TEXT NOT NULL DEFAULT '{}',
                    cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS repair_attempts (
                    session_id TEXT NOT NULL, number INTEGER NOT NULL,
                    run_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '{}',
                    reserved_cost_usd REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(session_id,number),
                    FOREIGN KEY(session_id) REFERENCES repair_sessions(id)
                );
            """)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def add_project(self, project: Project) -> None:
        """Register a logical project and reject conflicting duplicate IDs."""
        payload = json.dumps({**vars(project), "technologies": list(project.technologies)}, sort_keys=True)
        with self._connection() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (project.project_id,)).fetchone()
            if row is not None and row["payload"] != payload:
                raise ValueError("Project ID collision or incompatible project definition")
            db.execute("INSERT OR IGNORE INTO projects VALUES (?,?)", (project.project_id, payload))

    def get_project(self, project_id: str) -> Project | None:
        """Retrieve a project without trusting a local manifest as authorization."""
        with self._connection() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            return None
        obj = json.loads(row["payload"])
        obj["technologies"] = tuple(obj.get("technologies", []))
        return Project(**obj)

    def add_decision(self, decision: Decision) -> None:
        """Save a new decision; existing decision IDs are never overwritten."""
        with self._connection() as db:
            db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (
                decision.decision_id, decision.source_project_id, decision.revision,
                json.dumps(decision.to_dict(), sort_keys=True)))

    def get_decision(self, decision_id: str) -> Decision | None:
        """Load a decision from its canonical JSON representation."""
        with self._connection() as db:
            row = db.execute("SELECT payload FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return Decision.from_dict(json.loads(row["payload"])) if row else None

    def list_decisions(self, project_id: str | None = None) -> list[Decision]:
        """List decisions deterministically; optional source-project filter."""
        with self._connection() as db:
            if project_id is None:
                rows = db.execute("SELECT payload FROM decisions ORDER BY id").fetchall()
            else:
                rows = db.execute("SELECT payload FROM decisions WHERE source_project_id=? ORDER BY id", (project_id,)).fetchall()
        return [Decision.from_dict(json.loads(row["payload"])) for row in rows]

    def set_decision_status(self, decision_id: str, status: str, expected_revision: int) -> Decision:
        """Perform compare-and-swap approval/revocation in one transaction."""
        if status not in ("approved", "revoked"):
            raise ValueError("Only explicit approval or revocation is allowed")
        with self._connection() as db:
            row = db.execute("SELECT payload, revision FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if row is None:
                raise KeyError(decision_id)
            if row["revision"] != expected_revision:
                raise RevisionConflict("Decision was changed by another client")
            data = json.loads(row["payload"])
            if data["status"] == "revoked" and status == "approved":
                raise ValueError("Revoked decisions require a new revision and review")
            data.update(status=status, revision=expected_revision + 1)
            db.execute("UPDATE decisions SET payload=?, revision=? WHERE id=? AND revision=?", (
                json.dumps(data, sort_keys=True), expected_revision + 1, decision_id, expected_revision))
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise RevisionConflict("Concurrent decision modification")
        return Decision.from_dict(data)

    def bind_project_root(self, project_id: str, canonical_root: Path) -> None:
        """Bind a registered project ID to exactly one verified local checkout path.

        A copied `.decisionbound/project.yaml` is not sufficient to access an
        existing project's state. Registration is first-writer-wins under the
        SQLite writer transaction; relocating a project requires an explicit
        future migration and cannot be inferred from an identical manifest.
        """
        root = os.path.normcase(str(canonical_root.resolve(strict=True)))
        key = 'project_root:' + project_id
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM projects WHERE id=?', (project_id,)).fetchone() is None:
                raise PermissionError('Project must be registered before binding a checkout')
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            if row is not None and row['value'] != root:
                raise PermissionError('Project identity belongs to another registered checkout; copied manifests are not trusted')
            db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', (key,root))

    def require_project_root(self, project_id: str, canonical_root: Path) -> None:
        """Require current checkout binding; never authorize an unregistered clone.

        Old local databases with no binding need an explicit `dbound init`
        invocation to enroll their existing manifest before routine use.
        """
        expected = os.path.normcase(str(canonical_root.resolve(strict=True)))
        actual = self.get_setting('project_root:' + project_id)
        if actual is None:
            raise PermissionError('Project path is not enrolled; run dbound init explicitly')
        if actual != expected:
            raise PermissionError('Project manifest was copied into a different checkout')

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Get a user-scoped setting, e.g. the active profile name."""
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Atomically update a user-scoped setting."""
        with self._connection() as db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def list_profiles(self) -> list[dict]:
        """Return custom profiles, excluding secret values (never persisted)."""
        with self._connection() as db:
            rows = db.execute("SELECT payload FROM profiles ORDER BY name").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_profile(self, name: str) -> dict | None:
        """Retrieve a custom profile's public configuration."""
        with self._connection() as db:
            row = db.execute("SELECT payload FROM profiles WHERE name=?", (name,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_profile(self, profile: dict, expected_revision: int | None = None) -> dict:
        """Create or revision-check a profile without storing credentials."""
        name = profile["name"]
        with self._connection() as db:
            row = db.execute("SELECT revision FROM profiles WHERE name=?", (name,)).fetchone()
            if expected_revision is None:
                if row is not None:
                    raise RevisionConflict("Profile already exists")
                revision = 1
                data = {**profile, "revision": revision}
                db.execute("INSERT INTO profiles VALUES (?,?,?)", (name, revision, json.dumps(data, sort_keys=True)))
            else:
                if row is None or row["revision"] != expected_revision:
                    raise RevisionConflict("Stale profile revision")
                revision = expected_revision + 1
                data = {**profile, "revision": revision}
                db.execute("UPDATE profiles SET revision=?, payload=? WHERE name=? AND revision=?", (
                    revision, json.dumps(data, sort_keys=True), name, expected_revision))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RevisionConflict("Concurrent profile modification")
        return data

    def record_event(self, run_id: str, event: str, payload: dict) -> int:
        """Append a durable, timestamped run event."""
        with self._connection() as db:
            cur = db.execute("INSERT INTO run_events (run_id,event,payload) VALUES (?,?,?)", (
                run_id, event, json.dumps(payload, sort_keys=True)))
            return int(cur.lastrowid)

    def events(self, run_id: str) -> list[dict]:
        """Read events in insertion order for deterministic replay."""
        with self._connection() as db:
            rows = db.execute("SELECT id,event,payload,created_at FROM run_events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [{"id": r["id"], "event": r["event"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows]
