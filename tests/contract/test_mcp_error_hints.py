"""Every anticipated MCP tool error names what failed and one safe next step.

Each case calls the real tool function that the server registers. The message must
carry its next-step hint and must not echo the caller's input, a host path or a
secret-like token. The official SDK shows the model only ``ToolError`` messages, so
one test also checks the error type and the wire result when the SDK is installed.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys

import pytest

from decisionbound.context import ContextBudgetExceeded
from decisionbound.identity import init_project
from decisionbound.mcp_server import WorkflowNotFound, create_mcp_server
from decisionbound.review_gate import ReviewGate, ReviewUnavailable
from decisionbound.services import DecisionService
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask
from tests.contract.test_mcp_server_contract import FakeMCPServer

SECRET = "NEVER_EXPOSE_SECRET_VALUE"
UNKNOWN_ID = "wf_" + "0" * 32


def assert_safe(message: str, tmp_path, *echoed: str) -> None:
    """The message holds no host path, no secret-like text and none of the caller's input."""
    assert str(tmp_path) not in message
    assert "/" not in message and "\\" not in message
    assert SECRET not in message and "API_KEY" not in message and "sk-" not in message
    assert "Traceback" not in message and "Errno" not in message
    for text in echoed:
        assert text not in message


@pytest.fixture
def world(tmp_path, monkeypatch):
    state_home = tmp_path / "home"
    state_home.mkdir(mode=0o700)
    monkeypatch.setenv("DBOUND_HOME", str(state_home))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def calc(): pass\n")
    (root / ".env").write_text(f"API_KEY={SECRET}")
    (root / "notes.png").write_bytes(b"\x89PNG")
    (root / "pkg").mkdir()
    (root / "big.py").write_text("x = 1\n" * 120_000)
    store = SQLiteRepository(tmp_path / "state.db")
    init_project(root, store, owner="local")
    other = tmp_path / "other"
    other.mkdir()
    init_project(other, store, owner="local")
    journal = WorkflowJournal(store)
    spec = WorkflowSpec((WorkflowTask("inspect", "prepare", "Check architecture"),))
    own = journal.create(project_path=root, spec=spec, idempotency_key="workflow_own")
    foreign = journal.create(project_path=other, spec=spec, idempotency_key="workflow_foreign")
    server = create_mcp_server(root, store, FakeMCPServer)
    return server, store, root, own, foreign


def message(call, *args, **kwargs) -> tuple[BaseException, str]:
    with pytest.raises(Exception) as caught:
        call(*args, **kwargs)
    return caught.value, str(caught.value)


@pytest.mark.parametrize("path, kind, hint", [
    (".env", PermissionError, "Call search_project"),
    ("../../outside.py", PermissionError, "Call search_project"),
    ("/etc/passwd", ValueError, "Call search_project"),
    ("missing.py", FileNotFoundError, "Call search_project"),
    ("pkg", ValueError, "Call search_project"),
    ("notes.png", PermissionError, ".py"),
    ("big.py", PermissionError, "byte limit"),
])
def test_read_file_refusals_say_what_to_try_next(world, tmp_path, path, kind, hint):
    error, text = message(world[0].tools["read_file"], path)
    assert isinstance(error, kind)
    assert hint in text and "search_project" in text
    assert_safe(text, tmp_path, path)


def test_read_file_os_failure_does_not_leak_the_host_path(world, tmp_path, monkeypatch):
    from pathlib import Path

    def deny(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", deny)
    error, text = message(world[0].tools["read_file"], "main.py")
    assert isinstance(error, PermissionError)
    assert "search_project" in text and "operator" in text
    assert_safe(text, tmp_path, "main.py")


def test_search_project_skips_an_unreadable_file_and_still_answers(world, tmp_path, monkeypatch):
    from pathlib import Path

    def deny(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", deny)
    assert world[0].tools["search_project"]("calc")["results"] == []


def test_search_project_bad_query_names_the_bounds(world, tmp_path):
    for query in ("a", "x" * 121):
        error, text = message(world[0].tools["search_project"], query)
        assert isinstance(error, ValueError)
        assert "2 to 120 characters" in text
        assert_safe(text, tmp_path, query if len(query) > 1 else "no such text")


@pytest.mark.parametrize("task, tokens", [
    ("Explain calc", 99), ("Explain calc", 20_001), ("", 500), ("x" * 8001, 500), ("   \n", 500)])
def test_prepare_context_bounds_name_the_valid_ranges(world, tmp_path, task, tokens):
    error, text = message(world[0].tools["prepare_context"], task, max_tokens=tokens)
    assert isinstance(error, ValueError)
    assert "1 to 8000 characters" in text and "100 to 20000" in text
    assert_safe(text, tmp_path)


def test_prepare_context_budget_failure_says_to_raise_max_tokens(world, tmp_path, monkeypatch):
    def too_big(self, *args, **kwargs):
        raise ContextBudgetExceeded("Protected context exceeds budget")

    monkeypatch.setattr(DecisionService, "prepare", too_big)
    error, text = message(world[0].tools["prepare_context"], "Explain calc", max_tokens=100)
    assert isinstance(error, ContextBudgetExceeded)
    assert "larger max_tokens" in text and "20000" in text
    assert_safe(text, tmp_path)


@pytest.mark.parametrize("tool", ["get_workflow_status", "get_workflow_events",
                                  "get_workflow_attestation", "get_workflow_review_metadata"])
@pytest.mark.parametrize("workflow_id", [UNKNOWN_ID, "not-a-workflow-id"])
def test_unknown_workflow_says_where_to_get_an_id(world, tmp_path, tool, workflow_id):
    error, text = message(world[0].tools[tool], workflow_id)
    assert isinstance(error, KeyError) and isinstance(error, WorkflowNotFound)
    assert "dbound workflow submit" in text and "wf_" in text
    assert_safe(text, tmp_path, workflow_id)


@pytest.mark.parametrize("tool", ["get_workflow_status", "get_workflow_events",
                                  "get_workflow_attestation", "get_workflow_review_metadata"])
def test_other_projects_workflow_says_to_serve_that_project(world, tmp_path, tool):
    server, _, _, _, foreign = world
    error, text = message(server.tools[tool], foreign)
    assert isinstance(error, PermissionError)
    assert "does not belong to this MCP project" in text and "dbound mcp serve" in text
    assert_safe(text, tmp_path, foreign)


@pytest.mark.parametrize("reason, step", [
    ("Workflow has no terminal, unambiguous coding task", "retry once the code task"),
    ("Attempt is incomplete or unsafe to review", "after the coding attempt has finished"),
    ("Patch artifact is not a bounded regular file", "dbound review inspect"),
])
def test_review_refusals_name_a_next_step(world, tmp_path, monkeypatch, reason, step):
    def refuse(self, *args, **kwargs):
        raise ReviewUnavailable(reason)

    monkeypatch.setattr(ReviewGate, "snapshot", refuse)
    error, text = message(world[0].tools["get_workflow_review_metadata"], world[3])
    assert isinstance(error, ReviewUnavailable)
    assert text.startswith(reason + ". ") and step in text and "get_workflow_status" in text
    assert_safe(text, tmp_path, world[3])


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX git for the real check")
def test_review_of_a_workflow_without_a_coding_task_gets_a_real_hint(world, tmp_path):
    subprocess.run(["git", "-C", str(world[2]), "init", "-q"], check=True)
    error, text = message(world[0].tools["get_workflow_review_metadata"], world[3])
    assert isinstance(error, ReviewUnavailable)
    assert "get_workflow_status" in text
    assert_safe(text, tmp_path, world[3])


@pytest.mark.parametrize("tool, args", [
    ("list_project_decisions", ()), ("preview_transfer", ()), ("prepare_context", ("Explain calc",))])
def test_unreadable_decision_state_says_to_retry_then_ask_the_operator(world, tmp_path, monkeypatch, tool, args):
    server, store, *_ = world

    def broken(*a, **k):
        raise sqlite3.OperationalError(f"unable to open database file {tmp_path}/state.db")

    monkeypatch.setattr(store, "list_decisions", broken)
    error, text = message(server.tools[tool], *args)
    assert isinstance(error, RuntimeError)
    assert "Retry the call once" in text and "dbound state check" in text
    assert_safe(text, tmp_path)


@pytest.mark.parametrize("tool", ["get_workflow_status", "get_workflow_events",
                                  "get_workflow_attestation", "get_workflow_review_metadata"])
def test_unreadable_workflow_state_says_to_retry_then_ask_the_operator(world, tmp_path, monkeypatch, tool):
    def broken(self, *a, **k):
        raise sqlite3.DatabaseError(f"database disk image is malformed {tmp_path}")

    monkeypatch.setattr(WorkflowJournal, "status", broken)
    error, text = message(world[0].tools[tool], world[3])
    assert isinstance(error, RuntimeError)
    assert "Retry the call once" in text and "dbound state check" in text
    assert_safe(text, tmp_path)


def test_launch_refusals_tell_the_operator_how_to_check_the_enrolment(world, tmp_path):
    _, store, root, *_ = world
    bare = tmp_path / "bare"
    bare.mkdir()
    error, text = message(create_mcp_server, bare, store, FakeMCPServer)
    assert isinstance(error, FileNotFoundError)
    assert "dbound init" in text and "dbound status" in text
    copy = tmp_path / "copy"
    (copy / ".decisionbound").mkdir(parents=True)
    for part in (root / ".decisionbound").iterdir():
        (copy / ".decisionbound" / part.name).write_bytes(part.read_bytes())
    error, text = message(create_mcp_server, copy, store, FakeMCPServer)
    assert isinstance(error, PermissionError)
    assert "dbound init" in text and "dbound status" in text


def test_errors_are_toolerrors_so_the_official_sdk_shows_them(world):
    pytest.importorskip("mcp.server.mcpserver.exceptions")
    from mcp.server.mcpserver.exceptions import ToolError
    server, _, _, own, foreign = world
    for tool, arg in (("read_file", "missing.py"), ("search_project", "a"), ("prepare_context", ""),
                      ("get_workflow_status", UNKNOWN_ID), ("get_workflow_status", foreign)):
        call = server.tools[tool]
        error, _ = message(call, arg) if tool != "prepare_context" else message(call, arg, max_tokens=500)
        assert isinstance(error, ToolError), tool
