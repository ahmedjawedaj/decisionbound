"""Explicit, non-destructive schema upgrades for a single-user local SQLite state.

This adapter does not provide rolling distributed migrations. It produces a
verified *new* database file while preserving the original, including its WAL.
The user must stop old runtimes and explicitly activate the new path after
reviewing the migrated copy. No migration is performed on ordinary startup.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import sqlite3
import tempfile

from .state_integrity import (
    StateDestinationError, StateIntegrityError, _destination, _existing_regular,
    _hash_file, _publish, _read_only, inspect_database,
)
from .storage import SCHEMA_VERSION, SQLiteRepository


class StateMigrationError(StateIntegrityError):
    """An unsupported, unsafe, or unsuccessfully verified upgrade attempt."""


@dataclass(frozen=True)
class MigrationReport:
    """Public, metadata-only receipt for one non-overwriting state upgrade."""

    source_version: int
    target_version: int
    destination: str
    sha256: str
    event_count: int
    source_untouched: bool

    def to_dict(self) -> dict:
        """Serialize migration metadata without storing or returning source contents."""
        return asdict(self)


def migrate_database(source: str | Path, destination: str | Path) -> MigrationReport:
    """Prepare an upgraded, integrity-checked SQLite copy and preserve the source.

    Supports legacy v1 -> current and current -> current (validated export).
    Future, unversioned, and corrupt databases are rejected. A failed upgrade
    never publishes an output. Publication is atomic and refuses existing paths.

    This function does NOT activate the new database, change the active state
    location, or replace the source. A caller must stop all old processes before
    manually switching its state directory or restoring the migrated copy.
    """
    original = _existing_regular(source)
    target = _destination(destination)
    if original.resolve() == target.resolve():
        raise StateDestinationError("Cannot migrate onto the original database")

    # Read-only source preflight: an invalid or future schema cannot accidentally
    # be modified by the repository initializer or the destination writer.
    try:
        with closing(_read_only(original)) as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            integrity = db.execute("PRAGMA integrity_check").fetchall()
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != [("ok",)] or violations:
                raise StateMigrationError("Source integrity or foreign-key validation failed")
            if version not in (1, SCHEMA_VERSION):
                raise StateMigrationError(f"Unsupported source schema {version}; migration refused")
    except sqlite3.DatabaseError as exc:
        raise StateMigrationError("Source is not a valid readable SQLite database") from exc

    # The online SQLite backup captures a consistent committed snapshot even
    # while WAL writers are active, with no modification of their state file.
    temp: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".dbound-migrate-", suffix=".sqlite3")
        temp = Path(name)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with closing(_read_only(original)) as source_db, closing(sqlite3.connect(temp, timeout=10)) as copy:
            source_db.backup(copy, pages=128, sleep=0.02)

        # Revalidate schema version on the actual snapshot to avoid racing a
        # source replacement between the read-only preflight and backup.
        with closing(_read_only(temp)) as copied:
            snapshot_version = int(copied.execute("PRAGMA user_version").fetchone()[0])
        if snapshot_version != version:
            raise StateMigrationError("Source changed schema during snapshot; refusing upgrade")
        SQLiteRepository._upgrade_temporary_snapshot(temp)
        with closing(sqlite3.connect(temp, timeout=10)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")

        health = inspect_database(temp, full=True)
        if not health.ok:
            raise StateMigrationError("Migrated copy failed complete integrity checks")
        with temp.open("rb+") as data:
            os.fsync(data.fileno())
        digest = _hash_file(temp)
        _publish(temp, target)
        if _hash_file(target) != digest:
            raise StateMigrationError("Published migration digest mismatch; output is unsafe")
        return MigrationReport(version, SCHEMA_VERSION, str(target), digest,
                               health.event_count or 0, True)
    except (sqlite3.DatabaseError, OSError, RuntimeError) as exc:
        if isinstance(exc, StateMigrationError):
            raise
        raise StateMigrationError("SQLite migration failed without altering original state") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
            for extension in ("-wal", "-shm", "-journal"):
                temp.with_name(temp.name + extension).unlink(missing_ok=True)
