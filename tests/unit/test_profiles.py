"""User-facing configuration, override, and sharing contract tests."""
from pathlib import Path
import pytest
from decisionbound.profiles import ProfileManager, InvalidProfile, validate_profile
from decisionbound.storage import RevisionConflict, SQLiteRepository

@pytest.fixture
def manager(tmp_path):
    return ProfileManager(SQLiteRepository(tmp_path / "store.sqlite3"))


def test_safe_default_no_key(manager):
    assert manager.active() == "local-safe"
    assert manager.resolve("local-safe")["network"] is False


def test_builtins_and_custom_profiles(manager):
    assert {"claude", "codex", "qwen", "local", "openrouter"}.issubset(manager.names())
    created = manager.create("my-team", "codex", context_max_tokens=5000)
    assert created["revision"] == 1
    assert manager.resolve("my-team")["harness"] == "external-codex"
    assert manager.use("my-team")["name"] == "my-team"
    assert manager.active() == "my-team"


def test_profile_revision_is_checked(manager):
    original = manager.create("my-team", "claude")
    edited = manager.update({**original, "model": "configured-by-user"}, 1)
    assert edited["revision"] == 2
    with pytest.raises(RevisionConflict):
        manager.update(original, 1)


def test_secrets_and_unknown_keys_refused(manager):
    for extra in ({"api_key": "private"}, {"password": "private"}, {"admin": True}):
        with pytest.raises(InvalidProfile):
            manager.create("bad-profile", "claude", **extra)


def test_shareable_export_import(manager, tmp_path):
    manager.create("my-team", "qwen", max_steps=4)
    path = tmp_path / "team.yaml"
    manager.export("my-team", path)
    assert "secret" not in path.read_text().lower()
    other = ProfileManager(SQLiteRepository(tmp_path / "other.sqlite3"))
    assert other.import_file(path)["name"] == "my-team"
    assert other.resolve("my-team")["max_steps"] == 4


def test_profile_import_rejects_injected_key(manager, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: team-profile\nextends: codex\napi_key: leaked\n")
    with pytest.raises(InvalidProfile):
        manager.import_file(path)


def test_cannot_overwrite_builtin(manager):
    with pytest.raises(InvalidProfile):
        manager.update({"name": "claude", "extends": "codex"}, 1)
