"""Auditable, offline SQLite health checks and consistent backup/recovery.

This module deliberately operates on *one local user's* DecisionBound database.
It does not provide distributed transactions, disaster-recovery automation, or
transparent schema migration.  Backups contain private project/task metadata;
callers must handle their paths as sensitive user data.

Extension contract: tools may wrap ``inspect_database`` and
``create_backup`` for user interfaces.  They must not bypass the no-overwrite,
read-only source, atomic publication, or schema/integrity checks.  Recovery
always creates a separate file and never replaces an active runtime database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .storage import SCHEMA_VERSION, SCHEMA_REQUIRED_COLUMNS

_REQUIRED_TABLES = frozenset({"projects", "decisions", "profiles", "settings", "run_events", "repair_sessions", "repair_attempts"})


class StateIntegrityError(RuntimeError):
    """Source snapshot failed integrity validation, or cannot be read safely."""


class StateDestinationError(ValueError):
    """Unsafe backup destination, overwrite request or incompatible recovery target."""


@dataclass(frozen=True)
class StateHealth:
    """Credential-free report; a healthy database is not a production certification.

    The report records schema and integrity validation, but contains no raw
    project names, decisions, API credentials, prompt text, or stored events.
    """

    ok: bool
    schema_version: int | None
    expected_schema_version: int
    size_bytes: int
    wal_bytes: int
    journal_mode: str
    required_tables_present: bool
    schema_columns_valid: bool
    integrity: str
    foreign_keys_ok: bool
    event_count: int | None

    def to_dict(self) -> dict[str, Any]:
        """Expose stable JSON-compatible read-only diagnostics."""
        return asdict(self)


@dataclass(frozen=True)
class StateSnapshot:
    """Successful consistent snapshot, including its independently checked hash."""

    path: str
    sha256: str
    size_bytes: int
    schema_version: int

    def to_dict(self) -> dict[str, Any]:
        """Return metadata only; snapshot bytes are never emitted to stdout."""
        return asdict(self)


def _existing_regular(path: str | Path) -> Path:
    """Require an existing regular non-symlink file without auto-creating it."""
    p = Path(path).expanduser().absolute()
    if p.is_symlink() or not p.is_file():
        raise StateIntegrityError("Database must be an existing regular non-symlink file")
    return p


def _read_only(path: Path) -> sqlite3.Connection:
    """Connect with SQLite mode=ro, including correct escaping of URI metacharacters."""
    # Avoid URI parameter injection when a filename contains '?' or '#'.
    uri = path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def inspect_database(path: str | Path, *, full: bool = False) -> StateHealth:
    """Validate an existing database without creating it or changing user state.

    Args:
        path: Existing SQLite database to inspect.
        full: Run PRAGMA integrity_check rather than the faster quick_check.
            Full checks are useful before release or recovery but can take
            longer for large user databases.

    Returns:
        A non-secret report for both passing and failing integrity checks.

    Raises:
        StateIntegrityError: Invalid source path or unopenable/corrupt SQLite.
    """
    source = _existing_regular(path)
    try:
        with closing(_read_only(source)) as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            mode = str(db.execute("PRAGMA journal_mode").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            check = "integrity_check" if full else "quick_check"
            results = [row[0] for row in db.execute(f"PRAGMA {check}").fetchall()]
            fk_results = db.execute("PRAGMA foreign_key_check").fetchall()
            required = _REQUIRED_TABLES <= tables
            columns_valid = all(
                required_columns <= {row[1] for row in db.execute(f'PRAGMA table_info("{name}")')}
                for name, required_columns in SCHEMA_REQUIRED_COLUMNS.items()
            )
            count = int(db.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]) if "run_events" in tables else None
    except (sqlite3.DatabaseError, OSError) as exc:
        raise StateIntegrityError("Database is not readable as a valid SQLite store") from exc
    integrity = "ok" if results == ["ok"] else "failed"
    return StateHealth(
        ok=(integrity == "ok" and not fk_results and required and columns_valid and version == SCHEMA_VERSION),
        schema_version=version,
        expected_schema_version=SCHEMA_VERSION,
        size_bytes=source.stat().st_size,
        wal_bytes=_wal_size(source),
        journal_mode=mode,
        required_tables_present=required,
        schema_columns_valid=columns_valid,
        integrity=integrity,
        foreign_keys_ok=not fk_results,
        event_count=count,
    )


def _wal_size(source: Path) -> int:
    """Return the size of the write-ahead log, or 0 when there is none.

    SQLite deletes the -wal file when the last connection closes, so a concurrent writer can remove it at any
    moment. Asking for the size directly avoids a check-then-stat race that raised FileNotFoundError.
    """
    try:
        return source.with_name(source.name + "-wal").stat().st_size
    except FileNotFoundError:
        return 0


def _destination(path: str | Path) -> Path:
    """Validate a new output path with no existing target or symlink parent."""
    target = Path(path).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise StateDestinationError("Destination already exists; overwriting is forbidden")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise StateDestinationError("Destination parent must be an existing non-symlink directory")
    return target


def _hash_file(path: Path) -> str:
    """Stream digest calculation without loading a potentially large DB in memory."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _publish(temp: Path, target: Path) -> None:
    """Publish a validated file atomically without ever overwriting a target.

    POSIX hard-link publication and Windows rename-if-absent avoid TOCTOU
    between the preflight existence check and publication. All temporary
    files reside on the same filesystem as their final destination.
    """
    try:
        if os.name == "nt":  # Windows rename raises FileExistsError for an existing target.
            os.rename(temp, target)
        else:
            os.link(temp, target, follow_symlinks=False)
        if os.name == "posix":
            os.chmod(target, 0o600)
        # Best-effort sync the parent directory after new directory entry publication.
        if os.name == "posix":
            try:
                fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                # The file may already be published: report ambiguous directory
                # persistence explicitly, never falsely claim "no output".
                raise StateIntegrityError(
                    "Snapshot published but parent directory durability is not confirmed; inspect output"
                ) from exc
    except FileExistsError as exc:
        raise StateDestinationError("Destination was created concurrently; refusing overwrite") from exc


def _snapshot(source: Path, destination: Path) -> StateSnapshot:
    """Use SQLite's online backup API; validate before atomically publishing."""
    source_health = inspect_database(source, full=True)
    if not source_health.ok:
        raise StateIntegrityError("Source database failed schema or integrity validation")
    temp: Path | None = None
    try:
        fd, filename = tempfile.mkstemp(prefix=".dbound-backup-", suffix=".tmp", dir=destination.parent)
        temp = Path(filename)
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        os.close(fd)
        # Source remains read-only, while online backup yields a consistent
        # committed snapshot even with active WAL readers and writers.
        with closing(_read_only(source)) as original, closing(sqlite3.connect(temp, timeout=10)) as copy:
            original.backup(copy, pages=128, sleep=0.02)
        health = inspect_database(temp, full=True)
        if not health.ok:
            raise StateIntegrityError("Generated snapshot failed integrity validation")
        with temp.open("rb+") as data:
            os.fsync(data.fileno())
        digest = _hash_file(temp)
        _publish(temp, destination)
        if _hash_file(destination) != digest:
            raise StateIntegrityError("Published snapshot digest mismatch; destination must be discarded")
        return StateSnapshot(str(destination), digest, destination.stat().st_size, health.schema_version or 0)
    except (sqlite3.DatabaseError, OSError) as exc:
        raise StateIntegrityError("Failed to create a consistent SQLite snapshot") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                temp.with_name(temp.name + suffix).unlink(missing_ok=True)


def create_backup(source: str | Path, destination: str | Path) -> StateSnapshot:
    """Create an immutable point-in-time backup without stopping the local API.

    No network services are used. The output is sensitive and should never
    be checked into Git. Nothing replaces the live database. Any existing
    destination is refused, even if it is empty.
    """
    existing = _existing_regular(source)
    target = _destination(destination)
    if existing.resolve() == target.resolve():
        raise StateDestinationError("Cannot back up over the active database")
    return _snapshot(existing, target)


def restore_to_new_path(backup: str | Path, destination: str | Path,
                        *, active_path: str | Path | None = None) -> StateSnapshot:
    """Validate a backup and recover into a *new*, never-overwritten DB file.

    The caller may then inspect the recovered file and migrate it through a
    separately approved procedure. This operation is deliberately not an
    in-place recovery command: replacing an active WAL database can destroy
    in-flight work or cause split-brain state across running processes.
    """
    source = _existing_regular(backup)
    target = _destination(destination)
    if source.resolve() == target.resolve():
        raise StateDestinationError("Recovery source and destination must differ")
    if active_path is not None and target.resolve() == Path(active_path).expanduser().absolute().resolve():
        raise StateDestinationError("Cannot restore directly over the active state path")
    return _snapshot(source, target)
