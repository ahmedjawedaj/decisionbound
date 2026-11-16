"""Deterministic, adversarial end-user setup tests. No external credentials."""
from pathlib import Path
import pytest
from decisionbound.onboarding import SetupService
from decisionbound.profiles import ProfileManager
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository

@pytest.fixture
def state(tmp_path):
    store = SQLiteRepository(tmp_path / 'home' / 'state.sqlite3')
    return tmp_path, store, SetupService(store, environ={}, which=lambda _: None)


def test_safe_default_is_offline_and_not_authenticating(state):
    _, _, service = state
    status = service.inspect()
    assert status.ready and status.profile == 'local-safe'
    assert status.integration_tested is False
    assert not any(x.key == 'provider_credential' for x in status.checks)
    assert not any(x.ok for x in status.checks if x.key == 'write_consent')


def test_custom_profile_inherits_provider_and_requires_external_cli(state):
    _, store, service = state
    ProfileManager(store).create('my-codex', 'codex', max_steps=2)
    status = service.configure('my-codex')
    assert not status.ready
    assert status.harness == 'external-codex'
    assert status.next_action == 'Install and authenticate the codex CLI'


def test_credential_only_checks_presence_and_never_discloses(state):
    _, store, _ = state
    value = 'super-sensitive-example-credential'
    service = SetupService(store, environ={'OPENROUTER_API_KEY': value}, which=lambda _: None)
    report = service.inspect('openrouter')
    assert report.ready and not report.integration_tested
    assert value not in str(report.to_dict())
    assert any(c.key == 'provider_credential' for c in report.checks)


def test_missing_key_reports_name_not_value(state):
    _, _, service = state
    report = service.inspect('qwen')
    assert not report.ready and 'QWEN_API_KEY' in report.next_action


def test_native_claude_present_does_not_claim_integration(state):
    _, store, _ = state
    service = SetupService(store, environ={}, which=lambda command: '/fake/claude' if command == 'claude' else None)
    report = service.inspect('claude')
    assert report.ready and not report.integration_tested
    assert 'bounded integration test' in report.next_action


def test_ollama_not_running_does_not_claim_success(state):
    _, store, _ = state
    service = SetupService(store, which=lambda command: '/fake/ollama' if command == 'ollama' else None)
    report = service.inspect('local')
    assert report.ready and 'not tested' in report.checks[0].detail


def test_profile_selection_does_not_initialize_project_implicitly(state):
    root, store, service = state
    repo = root / 'repo'; repo.mkdir()
    report = service.configure('local-safe', repo)
    assert report.project_registered is False
    assert not (repo / '.decisionbound').exists()


def test_explicit_initialization_registers_same_identity_as_cli(state):
    root, store, service = state
    repo = root / 'repo'; repo.mkdir()
    report = service.configure('local-safe', repo, initialize_project=True)
    assert report.ready and report.project_registered
    assert len(list((repo / '.decisionbound').iterdir())) == 1
    assert service.configure('local-safe', repo, initialize_project=True).ready


def test_foreign_project_manifest_is_never_silently_claimed(state):
    root, store, service = state
    foreign = root / 'foreign'; foreign.mkdir()
    foreign_store = SQLiteRepository(root / 'foreign_state.sqlite3')
    init_project(foreign, foreign_store)
    with pytest.raises(PermissionError):
        service.configure('codex', foreign, initialize_project=True)
    assert ProfileManager(store).active() == 'local-safe'
    assert service.inspect(project_path=foreign).project_registered is False


def test_symlinked_manifest_cannot_be_initialized(state):
    root, _, service = state
    repo = root / 'repo'; repo.mkdir()
    metadata = repo / '.decisionbound'; metadata.mkdir()
    other = root / 'source.yaml'; other.write_text('bad')
    (metadata / 'project.yaml').symlink_to(other)
    with pytest.raises((ValueError, FileNotFoundError)):
        service.configure('local-safe', repo, initialize_project=True)


def test_invalid_profile_or_missing_project_leaves_profile_unchanged(state):
    root, store, service = state
    with pytest.raises(KeyError):
        service.configure('unknown-profile')
    with pytest.raises(ValueError):
        service.configure('claude', initialize_project=True)
    with pytest.raises(FileNotFoundError):
        service.configure('claude', root / 'missing', initialize_project=True)
    assert ProfileManager(store).active() == 'local-safe'


def test_writes_are_never_approved_by_profile_selection(state):
    _, store, service = state
    for name in ProfileManager(store).names():
        status = service.configure(name)
        assert any(c.key == 'write_consent' and not c.ok for c in status.checks)
        assert status.integration_tested is False
