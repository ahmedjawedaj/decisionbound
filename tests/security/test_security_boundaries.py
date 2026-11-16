"""Adversarial tests for localhost/auth, symlink and secret-file boundaries."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from decisionbound.web import create_app, local_token
from decisionbound.profiles import validate_profile, InvalidProfile
from decisionbound.agent_loop import repository_readonly_tools


@pytest.fixture(autouse=True)
def private_home(monkeypatch, tmp_path):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "home"))


def test_forged_host_and_origin_rejected():
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {local_token()}"}
    assert client.get("/api/profiles", headers={**headers, "Host": "attacker.example"}).status_code == 403
    assert client.get("/api/profiles", headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/profiles", headers=headers).status_code == 200


def test_credentials_do_not_enter_shareable_profiles():
    with pytest.raises(InvalidProfile):
        validate_profile({"name": "team-profile", "model": "sk-1234567890abcdefghijklmnop"})


def test_symlinked_credentials_are_not_inspectable(tmp_path):
    (tmp_path / ".env").write_text("SECRET_MARKER=do-not-read")
    (tmp_path / "alias.py").symlink_to(tmp_path / ".env")
    tools = repository_readonly_tools(tmp_path)
    with pytest.raises(PermissionError):
        tools.call("read_file", {"path": "alias.py"}, {"read_file"})
    output = tools.call("search_project", {"query": "SECRET_MARKER"}, {"search_project"})
    assert not output["results"]


def test_token_cannot_be_symlinked(tmp_path):
    from decisionbound.web import local_token
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    secret = tmp_path / "private.txt"
    secret.write_text("not-a-token")
    (home / "local_token").symlink_to(secret)
    with pytest.raises(ValueError):
        local_token()


def test_readonly_repository_tools_reject_symlinked_parent_directory(tmp_path):
    """No tool should follow a symlinked directory, even if it points inside the project."""
    from decisionbound.agent_loop import repository_readonly_tools
    real = tmp_path / "actual"
    real.mkdir()
    (real / "inside.py").write_text("PASSWORD=SHOULD_NOT_READ")
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    tools = repository_readonly_tools(tmp_path)
    with pytest.raises(PermissionError):
        tools.call("read_file", {"path": "alias/inside.py"}, {"read_file"})
