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
