"""Real-file adversarial tests for consistent local state snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest
from typer.testing import CliRunner

from decisionbound.cli import app
from decisionbound.storage import SQLiteRepository, SCHEMA_VERSION
from decisionbound.state_integrity import (
    StateDestinationError, StateIntegrityError, create_backup,
    inspect_database, restore_to_new_path,
)


@pytest.fixture
def live(tmp_path):
    store = SQLiteRepository(tmp_path / "user" / "state.sqlite3")
    for idx in range(5):
        store.record_event("fixture", "step", {"sequence": idx})
    return store


def test_health_existing_store_does_not_mutate(live):
    before = live.path.stat().st_mtime_ns
    report = inspect_database(live.path, full=True)
    assert report.ok
    assert report.schema_version == SCHEMA_VERSION
    assert report.event_count == 5
    assert report.wal_bytes >= 0 and report.journal_mode == "wal"
    assert report.foreign_keys_ok and report.required_tables_present
    assert live.path.stat().st_mtime_ns == before


def test_health_survives_the_wal_file_vanishing_mid_check(live, monkeypatch):
    """A concurrent writer closing the last connection deletes the -wal file at any moment."""
    wal = live.path.with_name(live.path.name + "-wal")
    wal.write_bytes(b"x" * 10)
    real_stat = Path.stat

    def vanishing(self, *args, **kwargs):
        if self.name.endswith("-wal"):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *args, **kwargs)

    real_is_file = Path.is_file
    # The race window: the file exists when it is checked and is gone when its size is read.
    monkeypatch.setattr(Path, "is_file", lambda self: True if self.name.endswith("-wal") else real_is_file(self))
    monkeypatch.setattr(Path, "stat", vanishing)
    assert inspect_database(live.path).wal_bytes == 0


def test_health_does_not_hide_a_permission_error_on_the_wal_file(live, monkeypatch):
    """Only a missing file counts as no WAL. Any other failure to read it must stay visible."""
    real_stat = Path.stat

    def denied(self, *args, **kwargs):
        if self.name.endswith("-wal"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError):
        inspect_database(live.path)


def test_health_reports_the_wal_size_when_present(live):
    wal = live.path.with_name(live.path.name + "-wal")
    wal.write_bytes(b"x" * 10)
    assert inspect_database(live.path).wal_bytes == wal.stat().st_size


def test_missing_database_never_auto_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(StateIntegrityError):
        inspect_database(path)
    assert not path.exists()


def test_backup_restores_consistent_events(live, tmp_path):
    backup = tmp_path / ("consistent snapshot ?.sqlite3" if os.name == "posix" else "consistent snapshot #.sqlite3")
    snapshot = create_backup(live.path, backup)
    assert snapshot.sha256 == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert snapshot.schema_version == SCHEMA_VERSION
    assert backup.stat().st_size == snapshot.size_bytes
    assert backup.is_file()
    if os.name == "posix":
        assert backup.stat().st_mode & 0o077 == 0
    assert inspect_database(backup).event_count == 5
    live.record_event("fixture", "later", {"sequence": 999})
    assert inspect_database(live.path).event_count == 6
    assert inspect_database(backup).event_count == 5
    restored = tmp_path / "recovered.sqlite3"
    restore_to_new_path(backup, restored, active_path=live.path)
    assert SQLiteRepository(restored).events("fixture")[-1]["payload"]["sequence"] == 4


def test_backup_never_overwrites_file_or_symlink(live, tmp_path):
    path = tmp_path / "existing.sqlite3"
    path.write_bytes(b"original")
    with pytest.raises(StateDestinationError):
        create_backup(live.path, path)
    assert path.read_bytes() == b"original"
    if hasattr(os, "symlink"):
        path.unlink()
        try:
            path.symlink_to(live.path)
        except OSError:
            # Some Windows hosts do not permit non-admin symlink creation.
            pytest.skip("OS does not permit symlink creation for this user")
        with pytest.raises(StateDestinationError):
            create_backup(live.path, path)
        assert live.path.exists()


def test_incompatible_or_invalid_backup_rejected(live, tmp_path):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not sqlite")
    with pytest.raises(StateIntegrityError):
        inspect_database(garbage)
    with pytest.raises(StateIntegrityError):
        restore_to_new_path(garbage, tmp_path / "refuse.db")
    assert not (tmp_path / "refuse.db").exists()

    alternate = tmp_path / "unrelated.db"
    with sqlite3.connect(alternate) as db:
        db.execute("CREATE TABLE arbitrary (n INTEGER)")
    assert not inspect_database(alternate).ok
    with pytest.raises(StateIntegrityError):
        create_backup(alternate, tmp_path / "wrong.db")


def test_active_db_and_double_restore_are_forbidden(live, tmp_path):
    backup = tmp_path / "backup.db"
    create_backup(live.path, backup)
    with pytest.raises(StateDestinationError):
        restore_to_new_path(backup, live.path, active_path=live.path)
    target = tmp_path / "new.db"
    restore_to_new_path(backup, target, active_path=live.path)
    with pytest.raises(StateDestinationError):
        restore_to_new_path(backup, target, active_path=live.path)


def test_snapshot_clean_failure_preserves_existing_state(live, tmp_path, monkeypatch):
    from decisionbound import state_integrity
    target = tmp_path / "failure.db"

    def fail_before_publish(*args):
        raise OSError("injected I/O failure")

    monkeypatch.setattr(state_integrity, "_publish", fail_before_publish)
    with pytest.raises(StateIntegrityError):
        create_backup(live.path, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".dbound-backup-*"))
    assert inspect_database(live.path).ok


def test_snapshot_during_concurrent_wal_writes(live, tmp_path):
    # The resulting backup represents one committed point in time, never half
    # a transaction, even while separate writer connections append events.
    gate = threading.Event()
    errors = []

    def writer():
        gate.wait()
        try:
            for i in range(55):
                live.record_event("writer", "append", {"count": i})
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=writer)
    thread.start()
    gate.set()
    snapshot = create_backup(live.path, tmp_path / "under-load.db")
    thread.join(timeout=20)
    assert not thread.is_alive() and not errors
    assert snapshot.size_bytes > 0
    copy = SQLiteRepository(snapshot.path)
    steps = [e["payload"]["count"] for e in copy.events("writer")]
    assert steps == list(range(len(steps)))
    assert inspect_database(snapshot.path).ok


def test_concurrent_backup_target_only_one_wins(live, tmp_path):
    target = tmp_path / "only-one.db"

    def copy():
        try:
            create_backup(live.path, target)
            return "ok"
        except StateDestinationError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: copy(), range(8)))
    assert statuses.count("ok") == 1, statuses
    assert statuses.count("duplicate") == 7, statuses
    assert inspect_database(target).ok


def test_cli_full_check_backup_restore(tmp_path, monkeypatch, live):
    monkeypatch.setenv("DBOUND_HOME", str(live.path.parent))
    runner = CliRunner()
    check = runner.invoke(app, ["state", "check", "--full"])
    assert check.exit_code == 0, (check.output, check.exception)
    assert json.loads(check.output)["ok"]
    backup = tmp_path / "from-cli.db"
    result = runner.invoke(app, ["state", "backup", "--output", str(backup)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(json.loads(result.output)["sha256"]) == 64
    other = tmp_path / "restored-cli.db"
    result = runner.invoke(app, ["state", "restore", "--backup", str(backup), "--destination", str(other)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert inspect_database(other).ok
    assert runner.invoke(app, ["state", "restore", "--backup", str(backup), "--destination", str(live.path)]).exit_code != 0


def test_cli_missing_state_reports_failure_without_creation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DBOUND_HOME", str(home))
    result = CliRunner().invoke(app, ["state", "check"])
    assert result.exit_code == 2
    assert json.loads(result.output)["ok"] is False
    assert not home.exists()


def test_backup_does_not_echo_sensitive_stored_data(live, tmp_path, monkeypatch):
    live.record_event("fixture", "sensitive", {"private_canary": "do-not-log-this"})
    monkeypatch.setenv("DBOUND_HOME", str(live.path.parent))
    output = CliRunner().invoke(app, ["state", "backup", "--output", str(tmp_path / "canary.db")])
    assert output.exit_code == 0
    assert "do-not-log-this" not in output.output
    assert "private_canary" not in output.output


def test_backup_does_not_copy_uncommitted_wal_transaction(live, tmp_path):
    with sqlite3.connect(live.path, timeout=5) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO run_events(run_id,event,payload) VALUES (?,?,?)",
                     ("unstable", "pending", '{"not_committed":true}'))
        backup = tmp_path / "committed-only.db"
        snapshot = create_backup(live.path, backup)
        assert inspect_database(snapshot.path).event_count == 5
        conn.commit()
    assert inspect_database(live.path).event_count == 6
    assert inspect_database(snapshot.path).event_count == 5


def test_special_character_and_unicode_filenames_supported(live, tmp_path):
    # "?" is reserved in Windows file names; keep it wherever the OS allows it.
    query = "?" if os.name == "posix" else "&"
    filename = tmp_path / f"copy # {query} ü 漢 字.db"
    create_backup(live.path, filename)
    assert inspect_database(filename, full=True).ok
    restored = tmp_path / f"restored # {query} héllo.db"
    restore_to_new_path(filename, restored)
    assert inspect_database(restored).ok


def test_large_audit_run_index_present_and_used(live):
    # Avoid a full scan of every user's event on every per-run trace query.
    with sqlite3.connect(live.path) as db:
        db.executemany("INSERT INTO run_events(run_id,event,payload) VALUES (?,?,?)",
                       ((f"other_{i % 400}", "bulk", "{}") for i in range(12000)))
        plan = db.execute("EXPLAIN QUERY PLAN SELECT id,event,payload,created_at FROM run_events "
                          "WHERE run_id=? ORDER BY id", ("fixture",)).fetchall()
    assert any("idx_run_events_run_id_id" in row[-1] for row in plan), plan
    assert len(live.events("fixture")) == 5
    assert inspect_database(live.path).event_count == 12005


def test_published_snapshot_directory_fsync_failure_is_explicit(live, tmp_path, monkeypatch):
    # Failure *after* publication cannot be reported as "no output".
    if os.name != "posix":
        pytest.skip("Directory fsync persistence model is POSIX-specific")
    from decisionbound import state_integrity
    original = state_integrity.os.fsync

    def fail_directory_only(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory sync error")
        return original(fd)

    monkeypatch.setattr(state_integrity.os, "fsync", fail_directory_only)
    target = tmp_path / "post-publish.db"
    with pytest.raises(StateIntegrityError, match="published but parent directory durability"):
        create_backup(live.path, target)
    assert target.is_file() and inspect_database(target).ok


def test_header_corruption_refused_without_touching_active_backup(live, tmp_path):
    corrupted = tmp_path / "corrupt-header.db"
    create_backup(live.path, corrupted)
    with corrupted.open("r+b") as file:
        file.seek(0)
        file.write(b"Not SQLite header")
    with pytest.raises(StateIntegrityError):
        inspect_database(corrupted)
    with pytest.raises(StateIntegrityError):
        restore_to_new_path(corrupted, tmp_path / "not-published.db")
    assert inspect_database(live.path).ok
