"""Untrusted legacy SQLite upgrade tests: no mutation, no overwrites."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from decisionbound.cli import app
from decisionbound.state_integrity import inspect_database, StateDestinationError
from decisionbound.state_migration import migrate_database, StateMigrationError
from decisionbound.storage import SQLiteRepository, SCHEMA_VERSION


def create_legacy(path: Path) -> Path:
    """Build v1 store carrying real previous user data, not an empty fixture."""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE decisions (id TEXT PRIMARY KEY, source_project_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE profiles (name TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT INTO settings VALUES (?,?)', ('active_profile', 'local-safe'))
        db.execute('INSERT INTO run_events(run_id,event,payload) VALUES (?,?,?)', ('run_001', 'created', '{"kind":"original"}'))
        db.execute('PRAGMA user_version=1')
    return path


def test_legacy_upgrade_preserves_source_exactly(tmp_path):
    original = create_legacy(tmp_path/'legacy.sqlite3')
    old_bytes = original.read_bytes()
    with pytest.raises(RuntimeError, match='state migrate'):
        SQLiteRepository(original)
    assert original.read_bytes() == old_bytes
    upgraded = tmp_path / 'new.sqlite3'
    receipt = migrate_database(original, upgraded)
    assert receipt.source_untouched is True
    assert receipt.source_version == 1 and receipt.target_version == SCHEMA_VERSION
    assert len(receipt.sha256) == 64
    assert hashlib.sha256(upgraded.read_bytes()).hexdigest() == receipt.sha256
    assert original.read_bytes() == old_bytes
    assert inspect_database(upgraded, full=True).ok
    db = SQLiteRepository(upgraded)
    assert db.get_setting('active_profile') == 'local-safe'
    assert db.events('run_001')[0]['payload'] == {'kind': 'original'}
    assert not list(tmp_path.glob('.dbound-migrate-*'))


def test_migration_rejects_existing_output_without_touching_it(tmp_path):
    source = create_legacy(tmp_path/'old.db')
    output = tmp_path/'exists.db'
    output.write_bytes(b'original')
    with pytest.raises(StateDestinationError):
        migrate_database(source, output)
    assert output.read_bytes() == b'original'


def test_migration_rejects_invalid_and_future_schema(tmp_path):
    future = tmp_path/'future.db'
    with sqlite3.connect(future) as db:
        db.execute('PRAGMA user_version=912')
    with pytest.raises(StateMigrationError, match='Unsupported source'):
        migrate_database(future, tmp_path/'future-output.db')
    assert not (tmp_path/'future-output.db').exists()
    with pytest.raises(RuntimeError, match='Unsupported database schema'):
        SQLiteRepository(future)
    bogus = tmp_path/'bogus.db'
    bogus.write_bytes(b'not a database')
    with pytest.raises(StateMigrationError):
        migrate_database(bogus, tmp_path/'bogus-output.db')


def test_unversioned_occupied_db_not_silently_adopted(tmp_path):
    path = tmp_path/'unversioned.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE foreign_user_data (secret TEXT)')
        db.execute('INSERT INTO foreign_user_data VALUES (?)', ('do not destroy',))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='Unversioned database'):
        SQLiteRepository(path)
    assert path.read_bytes() == before
    with pytest.raises(StateMigrationError):
        migrate_database(path, tmp_path/'adopted.db')


def test_current_schema_can_be_exported_without_modifying_source(tmp_path):
    current = SQLiteRepository(tmp_path/'current.db')
    current.record_event('run_1', 'started', {'count': 1})
    before = inspect_database(current.path)
    report = migrate_database(current.path, tmp_path/'copy.db')
    assert report.source_version == report.target_version == SCHEMA_VERSION
    assert inspect_database(current.path).event_count == before.event_count
    assert inspect_database(report.destination, full=True).event_count == 1


def test_migration_concurrent_publication_one_wins(tmp_path):
    source = create_legacy(tmp_path/'legacy.db')
    target = tmp_path/'only-one.db'
    def perform(_):
        try:
            migrate_database(source, target)
            return 'ok'
        except StateDestinationError:
            return 'duplicate'
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(perform, range(4)))
    assert results.count('ok') == 1
    assert results.count('duplicate') == 3
    assert inspect_database(target).ok


def test_migration_failure_cleans_temp_and_preserves_original(tmp_path, monkeypatch):
    from decisionbound import state_migration
    original = create_legacy(tmp_path/'legacy.db')
    before = original.read_bytes()
    def fail(*args):
        raise OSError('failure injection')
    monkeypatch.setattr(state_migration, '_publish', fail)
    with pytest.raises(StateMigrationError):
        migrate_database(original, tmp_path/'failed.db')
    assert original.read_bytes() == before
    assert not (tmp_path/'failed.db').exists()
    assert not list(tmp_path.glob('.dbound-migrate-*'))


def test_cli_explicit_migration_and_refuse_active_overwrite(tmp_path):
    original = create_legacy(tmp_path/'legacy.db')
    output = tmp_path/'upgrade.db'
    result = CliRunner().invoke(app, ['state','migrate','--source',str(original),'--output',str(output)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output)['source_version'] == 1
    assert inspect_database(output).ok
    result = CliRunner().invoke(app, ['state','migrate','--source',str(original),'--output',str(output)])
    assert result.exit_code != 0


def test_no_symlink_migration_destination(tmp_path):
    original = create_legacy(tmp_path/'legacy.db')
    other = tmp_path/'other.db'
    other.write_bytes(b'x')
    link = tmp_path/'link.db'
    try:
        link.symlink_to(other)
    except (OSError, NotImplementedError):
        pytest.skip('symlink privilege unavailable')
    with pytest.raises(StateDestinationError):
        migrate_database(original, link)
    assert other.read_bytes() == b'x'


def test_current_schema_missing_column_is_not_healthy_or_opened(tmp_path):
    path = tmp_path/'invalid-current.db'
    with sqlite3.connect(path) as db:
        for name, columns in [
            ('projects','id TEXT PRIMARY KEY, payload TEXT NOT NULL'),
            ('decisions','id TEXT PRIMARY KEY, source_project_id TEXT, revision INTEGER, payload TEXT'),
            ('profiles','name TEXT PRIMARY KEY, revision INTEGER, payload TEXT'),
            ('settings','key TEXT PRIMARY KEY, value TEXT'),
            ('run_events','id INTEGER PRIMARY KEY, run_id TEXT, event TEXT, payload TEXT'),
            ('repair_sessions','id TEXT PRIMARY KEY'),
            ('repair_attempts','session_id TEXT PRIMARY KEY')]:
            db.execute(f'CREATE TABLE {name} ({columns})')
        db.execute('PRAGMA user_version=2')
    health = inspect_database(path, full=True)
    assert not health.ok and not health.schema_columns_valid
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match='missing or incompatible'):
        SQLiteRepository(path)
    assert path.read_bytes() == original


def test_incompatible_legacy_column_rejected_without_publish(tmp_path):
    path = tmp_path/'broken-v1.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE decisions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('PRAGMA user_version=1')
    with pytest.raises(StateMigrationError, match='SQLite migration failed'):
        migrate_database(path, tmp_path/'failed-v2.db')
    assert not (tmp_path/'failed-v2.db').exists()
    assert not list(tmp_path.glob('.dbound-migrate-*'))


def test_release_versioned_wheel_script_selects_only_expected_file(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[2]/'scripts'/'install_built_wheel.py'
    spec = importlib.util.spec_from_file_location('dbound_install_wheel', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path/'pyproject.toml').write_text('[project]\nversion="0.13.0a1"\n')
    dist = tmp_path/'dist'
    dist.mkdir()
    (dist/'decisionbound-0.12.0a1-py3-none-any.whl').write_bytes(b'old')
    with pytest.raises(FileNotFoundError):
        module.wheel_path(tmp_path)
    current = dist/'decisionbound-0.13.0a1-py3-none-any.whl'
    current.write_bytes(b'new')
    assert module.wheel_path(tmp_path) == current


def test_sensitive_state_and_keys_rejected_by_release_filter():
    import importlib.util
    script = Path(__file__).resolve().parents[2]/'scripts'/'build_release.py'
    spec = importlib.util.spec_from_file_location('dbound_release', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('id_rsa', 'secrets/private.key', 'backup.sqlite3', 'config/credentials.json', 'wallet.p12', '.decisionbound/user-state'):
        assert module._unsafe_release_path(name)
    for name in ('README.md', 'scripts/build_release.py', '.env.example', 'dist/decisionbound-0.13.0a1-py3-none-any.whl'):
        assert not module._unsafe_release_path(name)
