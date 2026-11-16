"""Offline MCP registration and least-privilege contract using a fake SDK class.

These tests validate our handlers, NOT the MCP wire protocol. A separate test
using the real v2 SDK runs on CI with the optional dependency installed.
"""
from pathlib import Path
import pytest
from decisionbound.mcp_server import create_mcp_server
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository


class FakeMCPServer:
    def __init__(self, name, description):
        self.name = name
        self.tools = {}
        self.resources = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate

    def resource(self, uri):
        def decorate(fn):
            self.resources[uri] = fn
            return fn
        return decorate


def test_mcp_handlers_scope_to_initialized_project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def calc(): pass\n")
    (root / ".env").write_text("API_KEY=NEVER_EXPOSE")
    store = SQLiteRepository(tmp_path / "state.db")
    init_project(root, store, owner="local")
    server = create_mcp_server(root, store, FakeMCPServer)
    assert {"project_info", "search_project", "read_file", "list_project_decisions",
            "preview_transfer", "prepare_context", "get_workflow_status", "get_workflow_events", "get_workflow_attestation", "get_workflow_review_metadata"} == set(server.tools)
    assert all("project_path" not in fn.__code__.co_varnames[:fn.__code__.co_argcount] for fn in server.tools.values())
    assert server.tools["search_project"]("calc")["results"][0]["file"] == "main.py"
    assert "NEVER_EXPOSE" not in str(server.tools["search_project"]("API_KEY"))
    assert server.tools["read_file"]("main.py")["path"] == "main.py"
    with pytest.raises(PermissionError):
        server.tools["read_file"](".env")
    with pytest.raises((ValueError, PermissionError)):
        server.tools["read_file"]("../../outside.py")
    assert server.tools["prepare_context"]("Explain calc", max_tokens=500)["task"] == "Explain calc"
    assert "decisionbound://project" in server.resources


def test_mcp_rejects_nonregistered_root(tmp_path):
    store = SQLiteRepository(tmp_path / "state.db")
    with pytest.raises(FileNotFoundError):
        create_mcp_server(tmp_path, store, FakeMCPServer)


def test_workflow_mcp_tools_are_project_scoped(tmp_path):
    from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask
    store = SQLiteRepository(tmp_path / 'state.db')
    first, second = tmp_path/'first', tmp_path/'second'
    first.mkdir(); second.mkdir()
    init_project(first, store); init_project(second, store)
    journal = WorkflowJournal(store)
    graph = WorkflowSpec((WorkflowTask('inspect', 'prepare', 'Check architecture'),))
    a = journal.create(project_path=first, spec=graph, idempotency_key='workflow_first')
    b = journal.create(project_path=second, spec=graph, idempotency_key='workflow_second')
    server = create_mcp_server(first, store, FakeMCPServer)
    assert server.tools['get_workflow_status'](a)['workflow_id'] == a
    assert server.tools['get_workflow_events'](a)[0]['event'] == 'created'
    assert server.tools['get_workflow_attestation'](a)['review_attestation'] == 'no_reviews'
    with pytest.raises(PermissionError):
        server.tools['get_workflow_status'](b)
    with pytest.raises(PermissionError):
        server.tools['get_workflow_events'](b)
    with pytest.raises(PermissionError):
        server.tools['get_workflow_attestation'](b)
