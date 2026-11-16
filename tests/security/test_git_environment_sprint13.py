"""Untrusted environment must not smuggle Git executable filters into worktrees."""
from __future__ import annotations
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from decisionbound.code_workspace import GitWorktreeManager, WorkspaceError, _git_env


def test_git_environment_filters_ambient_configuration(monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_COUNT','1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0','filter.injected.clean')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0','!/tmp/untrusted-hook')
    monkeypatch.setenv('GIT_SSH_COMMAND','unsafe-ssh')
    monkeypatch.setenv('SSH_ASKPASS','/tmp/fake-prompt')
    safe=_git_env()
    for name in ('GIT_CONFIG_COUNT','GIT_CONFIG_KEY_0','GIT_CONFIG_VALUE_0','GIT_SSH_COMMAND','SSH_ASKPASS'):
        assert name not in safe
    assert safe['GIT_CONFIG_GLOBAL']==os.devnull
    assert safe['GIT_CONFIG_NOSYSTEM']=='1'
    assert safe['GIT_CONFIG_SYSTEM']==os.devnull
    assert safe['GIT_TERMINAL_PROMPT']=='0'


def test_disallow_symlinked_workspace_root(tmp_path):
    real=tmp_path/'private'
    real.mkdir()
    alias=tmp_path/'state-alias'
    try:
        alias.symlink_to(real,target_is_directory=True)
    except (OSError,NotImplementedError):
        pytest.skip('Host does not support user symlinks')
    with pytest.raises(WorkspaceError,match='symlinked'):
        GitWorktreeManager(alias)
    assert not list(real.iterdir())
