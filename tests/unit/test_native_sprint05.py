"""Native agent compatibility: mocked subprocesses, no credentials or hosts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from decisionbound.code_native import (ClaudeCLIPatchProposer, CodexCLIPatchProposer,
                                       NativeCLIError)
from tests.integration.test_code_workflow import PATCH


def test_codex_command_readonly_schema_and_single_use(monkeypatch):
    called = []
    def fake_run(argv, **kwargs):
        called.append((argv, kwargs))
        assert argv[:2] == ['codex', 'exec']
        assert argv[argv.index('--sandbox')+1] == 'read-only'
        assert argv[argv.index('--ask-for-approval')+1] == 'never'
        assert '--output-schema' in argv
        assert kwargs['cwd'].is_dir()
        assert Path(argv[argv.index('--output-schema')+1]).is_file()
        assert 'selected_files' in kwargs['input']
        assert kwargs['cwd'] != Path.cwd()
        Path(argv[argv.index('--output-last-message')+1]).write_text(json.dumps({'patch': PATCH, 'summary': 'short'}))
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr('decisionbound.code_native.subprocess.run', fake_run)
    proposer = CodexCLIPatchProposer(model='gpt-6', enabled=True, acknowledged_unmetered=True)
    result = proposer.propose('change value', {'app.py': 'VALUE = 1\n'})
    assert result.patch == PATCH and len(called) == 1
    assert 'OPENAI_API_KEY' not in str(called[0][1]['input'])
    with pytest.raises(NativeCLIError, match='single-use'):
        proposer.propose('second', {'app.py': 'VALUE = 1\n'})


def test_native_adapters_reject_without_consent():
    with pytest.raises(PermissionError):
        CodexCLIPatchProposer(model='gpt-6', enabled=True).propose('task', {'app.py': 'foo'})
    with pytest.raises(PermissionError):
        ClaudeCLIPatchProposer(model='sonnet', max_budget_usd=0.2).propose('task', {'app.py': 'foo'})


def test_claude_tools_disabled_budget_and_structured_output(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv[0] == 'claude'
        assert argv[argv.index('--tools')+1] == ''
        assert '--strict-mcp-config' in argv
        assert '--no-session-persistence' in argv
        assert '--max-budget-usd' in argv
        assert '--json-schema' in argv
        assert kwargs['cwd'].is_dir()
        return subprocess.CompletedProcess(argv, 0,
            json.dumps({'structured_output': {'patch': PATCH, 'summary': 'small'}}), '')
    monkeypatch.setattr('decisionbound.code_native.subprocess.run', fake_run)
    proposal = ClaudeCLIPatchProposer(model='sonnet', max_budget_usd=0.2, enabled=True)
    assert proposal.propose('change value', {'app.py': 'VALUE = 1\n'}).patch == PATCH


def test_native_rejects_malformed_output_and_unknown_args(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({'result': {'patch': PATCH, 'summary': 's', 'permission':'yes'}}), '')
    monkeypatch.setattr('decisionbound.code_native.subprocess.run', fake_run)
    with pytest.raises(NativeCLIError):
        ClaudeCLIPatchProposer(model='sonnet', max_budget_usd=0.2, enabled=True).propose('x', {'app.py': 'old'})
    with pytest.raises(ValueError, match='model'):
        CodexCLIPatchProposer(model='--bypass')
    with pytest.raises(ValueError, match='context'):
        CodexCLIPatchProposer(model='gpt-6', enabled=True, acknowledged_unmetered=True).propose('x', {})


def test_native_proposal_failed_process_not_retried(monkeypatch):
    seen = []
    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 7, 'output', 'SECRET=never-log')
    monkeypatch.setattr('decisionbound.code_native.subprocess.run', fake_run)
    proposer = CodexCLIPatchProposer(model='gpt-6', enabled=True, acknowledged_unmetered=True)
    with pytest.raises(NativeCLIError, match='stderr deliberately not retained'):
        proposer.propose('x', {'app.py': 'text'})
    assert len(seen) == 1
    with pytest.raises(NativeCLIError, match='single-use'):
        proposer.propose('x', {'app.py': 'text'})


def test_native_provider_env_is_minimal(monkeypatch):
    monkeypatch.setenv('DBOUND_SHOULD_NOT_FORWARD_THIS', 'private')
    def fake_run(argv, **kwargs):
        assert 'DBOUND_SHOULD_NOT_FORWARD_THIS' not in kwargs['env']
        assert 'PYTHONPATH' not in kwargs['env']
        return subprocess.CompletedProcess(argv, 0,
            json.dumps({'structured_output': {'patch': PATCH, 'summary': 's'}}), '')
    monkeypatch.setattr('decisionbound.code_native.subprocess.run', fake_run)
    ClaudeCLIPatchProposer(model='sonnet', max_budget_usd=0.2, enabled=True).propose('x', {'app.py': 'old'})
