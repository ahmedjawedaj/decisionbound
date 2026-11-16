"""MCP review metadata must never leak patch text or cross a project scope."""
from __future__ import annotations
import pytest
from decisionbound.mcp_server import create_mcp_server
from decisionbound.identity import init_project
from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask
from tests.contract.test_mcp_server_contract import FakeMCPServer
from tests.integration.test_review_gate_sprint08 import completed


def test_review_tool_omits_diff_and_rejects_another_project(completed, tmp_path):
    store, state, first, workflow_id = completed
    second = tmp_path / 'second'
    second.mkdir()
    init_project(second, store)
    own = create_mcp_server(first, store, FakeMCPServer)
    foreign = create_mcp_server(second, store, FakeMCPServer)
    assert 'get_workflow_review_metadata' in own.tools
    data = own.tools['get_workflow_review_metadata'](workflow_id)
    assert data['workflow_id'] == workflow_id
    assert 'diff' not in data
    assert 'VALUE = 2' not in str(data)
    assert 'approve_export' not in own.tools and 'review_export' not in own.tools
    with pytest.raises(PermissionError, match='does not belong'):
        foreign.tools['get_workflow_review_metadata'](workflow_id)


def test_readonly_metadata_tool_cannot_authorize_or_export(completed):
    store, _, first, workflow_id = completed
    mcp = create_mcp_server(first, store, FakeMCPServer)
    mcp.tools['get_workflow_review_metadata'](workflow_id)
    with store._connection() as db:
        count = db.execute('SELECT COUNT(*) FROM local_export_reviews').fetchone()[0]
    assert count == 0
