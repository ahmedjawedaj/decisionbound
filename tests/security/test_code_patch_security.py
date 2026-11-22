"""Adversarial patches must fail before Git is allowed to write."""
from __future__ import annotations
import pytest
from pathlib import Path
from decisionbound.code_patches import PatchPolicy, PatchValidator, UnsafePatch
from tests.integration.test_code_workflow import PATCH, ADD, project  # noqa: F401


def patch_for(path: str, old='VALUE = 1', new='VALUE = 2') -> str:
    return f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n'


@pytest.mark.parametrize('path', [
    '../secret.py', 'x/../../secrets', '/tmp/outside', 'AGENTS.md', 'CLAUDE.md',
    '.env', '.env.production', '.github/workflows/ci.yml', '.git/config',
    '.decisionbound/project.yaml', '.gitattributes', 'private.pem', 'x/private.key',
    'name\\..\\other', 'bad//part', '-unsafe.py',
])
def test_protected_and_noncanonical_paths_are_rejected(project, path):
    root, state, db = project
    with pytest.raises(UnsafePatch):
        PatchValidator().inspect(patch_for(path), root)


@pytest.mark.parametrize('path', [
    'agents.md', 'Agents.MD', 'claude.md', '.ENV', '.Env.production', 'x/.ENV',
    '.GIT/config', '.Git/hooks/pre-commit', '.GitHub/Workflows/ci.yml', '.github/WORKFLOWS/ci.yml',
    '.DecisionBound/project.yaml', '.GitAttributes', 'sub/.GITATTRIBUTES', '.GITMODULES',
    'private.PEM', 'x/Private.Key', 'ID_RSA', 'x/Id_Ed25519.pub', 'x/KEY.P12',
], ids=str)
def test_protected_paths_are_matched_without_regard_to_case(project, path):
    """On macOS and Windows these name the protected files."""
    root, _, _ = project
    with pytest.raises(UnsafePatch, match='Protected repository path'):
        PatchValidator().inspect(patch_for(path), root)


@pytest.mark.parametrize('path', [
    'sub/AGENTS.md', 'a/b/CLAUDE.md', 'Sub/Agents.MD', 'docs/claude.md',
    '.envrc', 'sub/.envrc', '.ENVRC', 'secrets.env', 'config/Prod.ENV', 'a/b/local.env',
], ids=str)
def test_nested_agent_files_and_environment_files_are_protected(project, path):
    """An agent instruction file steers an agent from any directory, and an environment file holds secrets."""
    root, _, _ = project
    with pytest.raises(UnsafePatch, match='Protected repository path'):
        PatchValidator().inspect(patch_for(path), root)


@pytest.mark.parametrize('path', [
    'docs/agents-guide.md', 'src/environment.py', 'app/envrc.py', 'src/claude_client.py', 'notes/env.md',
], ids=str)
def test_names_that_only_resemble_protected_files_are_still_allowed(project, path):
    root, _, _ = project
    PatchPolicy().check_path(root, path)


@pytest.mark.parametrize('path', [
    'AGENTS.md.', '.git.', '.github./workflows/ci.yml', 'src./x.py', 'x/y.',
], ids=str)
def test_a_path_component_ending_in_a_dot_is_rejected(project, path):
    """Windows drops the trailing dot, so these name protected or existing files under another spelling."""
    root, _, _ = project
    with pytest.raises(UnsafePatch, match='noncanonical'):
        PatchValidator().inspect(patch_for(path), root)


@pytest.mark.parametrize('path', [
    '.env ', 'AGENTS.md ', '.git ', 'src /x.py', 'AGENTS.md:x', '.git::$INDEX_ALLOCATION', 'a.py:stream', 'a.',
], ids=str)
def test_check_path_alone_rejects_trailing_spaces_and_colons(project, path):
    """Acceptance contracts and source targets reach check_path without the diff header's character filter."""
    root, _, _ = project
    with pytest.raises(UnsafePatch, match='noncanonical'):
        PatchPolicy().check_path(root, path)


@pytest.mark.parametrize('path', ['Docs/Guide.md', 'README.MD', 'my-agents.md', 'src/Claude_notes.md', 'a/b.c.py'],
                         ids=str)
def test_ordinary_paths_with_capitals_or_similar_names_are_still_allowed(project, path):
    root, _, _ = project
    PatchPolicy().check_path(root, path)


def test_two_spellings_of_one_file_in_one_patch_are_a_duplicate(project):
    root, _, _ = project
    with pytest.raises(UnsafePatch, match='Duplicate'):
        PatchValidator().inspect(PATCH + patch_for('App.py'), root)
    # Another file in between must not hide the repeat.
    with pytest.raises(UnsafePatch, match='Duplicate'):
        PatchValidator().inspect(PATCH + patch_for('other.py') + patch_for('APP.PY'), root)


def test_symlink_target_and_parent_are_rejected(project, tmp_path):
    root, _, _ = project
    (root/'alias.py').symlink_to(root/'app.py')
    (root/'link').symlink_to(tmp_path, target_is_directory=True)
    for path in ('alias.py', 'link/malicious.py'):
        with pytest.raises(UnsafePatch, match='Symlink'):
            PatchValidator().inspect(patch_for(path), root)


@pytest.mark.parametrize('text', [
    'GIT binary patch\n',
    'diff --git a/app.py b/app.py\nold mode 100644\nnew mode 100755\n',
    'diff --git a/app.py b/app.py\nrename from app.py\nrename to new.py\n',
    'diff --git a/a.py b/b.py\n',
    'diff --git a/app.py b/app.py\n--- a/app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-VALUE = 1\n',
])
def test_unsupported_git_features_fail_closed(project, text):
    with pytest.raises(UnsafePatch):
        PatchValidator().inspect(text, project[0])


def test_duplicate_file_and_unbalanced_hunk_rejected(project):
    with pytest.raises(UnsafePatch):
        PatchValidator().inspect(PATCH + PATCH, project[0])
    with pytest.raises(UnsafePatch):
        PatchValidator().inspect(PATCH.replace('@@ -1 +1 @@', '@@ -1,2 +1,2 @@'), project[0])


def test_whitespace_and_limits_rejected(project):
    with pytest.raises(UnsafePatch):
        PatchValidator(PatchPolicy(max_bytes=32)).inspect(PATCH, project[0])
    with pytest.raises(UnsafePatch):
        PatchValidator(PatchPolicy(max_changed_lines=1)).inspect(PATCH, project[0])
    with pytest.raises(UnsafePatch):
        PatchValidator(PatchPolicy(allowed_globs=('tests/**',))).inspect(PATCH, project[0])


def test_added_file_regular_mode_supported(project):
    assert PatchValidator().inspect(ADD, project[0]).paths == ('new.py',)


def test_missing_newline_fails(project):
    with pytest.raises(UnsafePatch):
        PatchValidator().inspect(PATCH.rstrip('\n'), project[0])


def test_executable_tracked_file_rejected(project):
    root, _, _ = project
    import subprocess
    subprocess.run(['git','-C',str(root),'update-index','--chmod=+x','app.py'],check=True)
    # This must be refused regardless of index/worktree mismatch.
    with pytest.raises(UnsafePatch):
        PatchValidator().apply(PATCH, root)
