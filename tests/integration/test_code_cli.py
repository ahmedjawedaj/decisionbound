"""CLI exposes consent gates and run reconciliation without live providers."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from click import unstyle
from typer.testing import CliRunner
from decisionbound.cli import app
from tests.integration.test_code_workflow import PATCH, project  # noqa: F401


def test_cli_requires_explicit_two_factor_write_consent(project, tmp_path, monkeypatch):
    root, state, db = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    p = tmp_path/'change.patch'
    p.write_text(PATCH)
    runner = CliRunner()
    result = runner.invoke(app, ['code', 'apply', 'Fix bug', '--path', str(root),
                                 '--patch-file', str(p), '--image', 'python:3.12',
                                 '--test-cmd', 'python -m pytest -q'])
    assert result.exit_code != 0
    assert 'approve-writes' in unstyle(result.output)
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


def test_cli_lists_pending_runs(project, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(project[1]))
    result = CliRunner().invoke(app, ['code', 'pending'])
    assert result.exit_code == 0
    assert json.loads(result.stdout)['pending'] == []


def test_cli_cleanup_demands_operator_approval(project, monkeypatch):
    monkeypatch.setenv('DBOUND_HOME', str(project[1]))
    result = CliRunner().invoke(app, ['code', 'cleanup', 'code_'+'0'*32])
    assert result.exit_code != 0
    assert '--approve-cleanup' in unstyle(result.output)


def test_cli_inspect_is_read_only_and_reports_scope(project, tmp_path, monkeypatch):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    patch = tmp_path/'approved.patch'
    patch.write_text(PATCH)
    result = CliRunner().invoke(app, ['code', 'inspect', '--patch-file', str(patch), '--path', str(root)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data['paths'] == ['app.py'] and data['write_approved'] is False
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


def test_cli_mock_run_executes_only_disposable_checkout(project, tmp_path, monkeypatch):
    root, state, db = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    patch = tmp_path/'approved.patch'
    patch.write_text(PATCH)
    result = CliRunner().invoke(app, ['code', 'mock-run', 'Fix small bug', '--patch-file', str(patch),
                                     '--path', str(root), '--approve-writes'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert not data['acceptance_verified'] and not data['task_completed']
    assert data['tests'][0]['kind'] == 'mock'
    assert (root/'app.py').read_text() == 'VALUE = 1\n'
