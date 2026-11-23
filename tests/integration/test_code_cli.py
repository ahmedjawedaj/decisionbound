"""CLI exposes consent gates and run reconciliation without live providers."""
from __future__ import annotations
import json
import re
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


APPLY_ARGS = ['--image', 'python:3.12', '--test-cmd', 'python -m pytest -q']


@pytest.mark.parametrize('flags', [['--approve-writes'], ['--allow-docker']])
def test_code_apply_needs_both_flags_not_just_one(project, tmp_path, monkeypatch, flags):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    patch = tmp_path/'change.patch'
    patch.write_text(PATCH)
    result = CliRunner().invoke(app, ['code', 'apply', 'Fix bug', '--path', str(root),
                                      '--patch-file', str(patch), *APPLY_ARGS, *flags])
    assert result.exit_code != 0
    assert 'mandatory' in unstyle(result.output)  # the consent message, not the missing-image one
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


def test_code_mock_run_and_repair_mock_refuse_without_approve_writes(project, tmp_path, monkeypatch):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    patch = tmp_path/'change.patch'
    patch.write_text(PATCH)
    contract = tmp_path/'contract.json'
    contract.write_text('{}')
    mock = CliRunner().invoke(app, ['code', 'mock-run', 'Fix bug', '--path', str(root),
                                    '--patch-file', str(patch)])
    repair = CliRunner().invoke(app, ['code', 'repair-mock', 'Fix bug', '--path', str(root),
                                      '--contract', str(contract), '--key', 'k1',
                                      '--patch-file', str(patch)])
    assert mock.exit_code != 0 and 'approve-writes' in unstyle(mock.output)
    assert repair.exit_code != 0 and 'approve-writes' in unstyle(repair.output)
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


CONSENT_COMMANDS = {
    'propose': (['--model', 'm', '--input-price', '1', '--output-price', '1', '--target', 'app.py',
                 '--image', 'python:3.12', '--test-cmd', 'pytest'],
                ['--allow-remote', '--approve-writes', '--allow-docker']),
    'repair-provider': (['--contract', 'c.json', '--key', 'k1', '--model', 'm', '--input-price', '1',
                         '--output-price', '1', '--max-cost', '1', '--image', 'python:3.12'],
                        ['--allow-remote', '--allow-docker', '--approve-writes']),
    'native-propose': (['--contract', 'c.json', '--harness', 'claude', '--model', 'm',
                        '--image', 'python:3.12'],
                       ['--allow-native', '--allow-docker', '--approve-writes']),
}


@pytest.mark.parametrize('command', sorted(CONSENT_COMMANDS))
def test_model_and_native_write_commands_need_every_consent_flag(project, monkeypatch, command):
    """Each missing flag on its own must stop the command with the consent message."""
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    base, flags = CONSENT_COMMANDS[command]
    for missing in flags:
        given = [f for f in flags if f != missing]
        result = CliRunner().invoke(app, ['code', command, 'Fix bug', '--path', str(root), *base, *given])
        text = ' '.join(unstyle(result.output).split())
        assert result.exit_code != 0, missing
        assert text.count('Requires') >= 1 and all(f in text for f in flags), (missing, text)
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


def test_a_tag_image_in_strict_digest_mode_is_a_usage_error_not_a_traceback(project, tmp_path, monkeypatch):
    root, state, _ = project
    monkeypatch.setenv('DBOUND_HOME', str(state))
    monkeypatch.setenv('DBOUND_REQUIRE_IMAGE_DIGEST', '1')
    patch = tmp_path/'change.patch'
    patch.write_text(PATCH)
    result = CliRunner().invoke(app, ['code', 'apply', 'Fix bug', '--path', str(root), '--patch-file', str(patch),
                                      '--image', 'python:3.12', '--test-cmd', 'python -m pytest -q',
                                      '--approve-writes', '--allow-docker'])
    text = ' '.join(re.sub('[│╭╮╰╯─]', ' ', unstyle(result.output)).split())
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit)
    assert '--image' in text and 'sha256 digest' in text and 'Traceback' not in text
    assert (root/'app.py').read_text() == 'VALUE = 1\n'


def test_every_cli_call_site_builds_the_docker_runner_through_the_one_helper():
    """Five commands build the runner. Any new direct construction would bring the traceback back."""
    import ast
    import decisionbound.cli as cli
    tree = ast.parse(Path(cli.__file__).read_text(encoding='utf-8'))
    direct = [(node.lineno, getattr(parent, 'name', '')) for parent in ast.walk(tree)
              if isinstance(parent, ast.FunctionDef) for node in ast.walk(parent)
              if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'DockerTestRunner']
    assert {name for _, name in direct} == {'_docker_runner'}
    helper_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == '_docker_runner']
    assert len(helper_calls) == 5
