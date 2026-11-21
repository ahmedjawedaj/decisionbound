"""Fail-closed validation and application of small, text-only Git patches.

This is intentionally narrower than `git apply`: no deletions, executable
changes, symlinks, binary patches, path traversal, rename/copy, or protected
agent/CI/credential files. Review cannot be bypassed by a custom proposer.
"""
from __future__ import annotations
import fnmatch
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from .code_models import PatchInspection

_DIFF = re.compile(r'^diff --git a/([A-Za-z0-9_.+/-]+) b/([A-Za-z0-9_.+/-]+)$')
_HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$')
_BLOCKED = (
    '.git', '.git/**', '.decisionbound', '.decisionbound/**', '.github/workflows/**',
    '.env', '.env.*', '**/.env', '**/.env.*', '**/*.pem', '**/*.key', '**/*.p12',
    '**/id_rsa*', '**/id_ed25519*', '*.pem', '*.key', '*.p12',
    'id_rsa*', 'id_ed25519*', 'AGENTS.md', 'CLAUDE.md',
    '.gitattributes', '.gitmodules', '**/.gitattributes', '**/.gitmodules',
)


class UnsafePatch(ValueError):
    """A proposed patch violates the host's immutable safety policy."""


@dataclass(frozen=True)
class PatchPolicy:
    """Limits for text-only patches to regular, non-protected repository files.

    Custom callers can further restrict ``allowed_globs`` and limits, but
    cannot remove the built-in sensitive-file protections.
    """
    max_bytes: int = 96_000
    max_files: int = 8
    max_changed_lines: int = 600
    allowed_globs: tuple[str, ...] = ('**',)

    def __post_init__(self) -> None:
        if not 1 <= self.max_bytes <= 250_000 or not 1 <= self.max_files <= 32 or not 1 <= self.max_changed_lines <= 2000:
            raise ValueError('Invalid patch policy limits')
        if not self.allowed_globs or any(not x or '\\' in x for x in self.allowed_globs):
            raise ValueError('Invalid file allowlist')

    def check_path(self, workspace: Path, raw: str) -> None:
        """Reject traversal, symlinks (including ancestors), and protected files."""
        path = PurePosixPath(raw)
        if (raw.startswith('/') or not raw or '\\' in raw or path.as_posix() != raw or
                path.is_absolute() or any(p in ('', '.', '..') for p in path.parts) or
                len(raw) > 240 or raw.startswith('-')):
            raise UnsafePatch('Unsafe or noncanonical patch path')
        if any(fnmatch.fnmatch(raw, pat) for pat in _BLOCKED):
            raise UnsafePatch('Protected repository path')
        if not any(fnmatch.fnmatch(raw, p) for p in self.allowed_globs):
            raise UnsafePatch('Path outside caller allowlist')
        current = workspace.resolve(strict=True)
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise UnsafePatch('Symlink in path')
            if current.exists() and not (current.is_file() or current.is_dir()):
                raise UnsafePatch('Nonregular path component')
        if current.exists() and not current.is_file():
            raise UnsafePatch('Target is not a regular file')


class PatchValidator:
    """Validate a patch before Git is allowed to touch an isolated checkout."""

    def __init__(self, policy: PatchPolicy | None = None):
        self.policy = policy or PatchPolicy()

    def inspect(self, patch: str, workspace: Path) -> PatchInspection:
        """Validate format, paths, modes and limits, returning scope receipts.

        A second independent ``git apply --check`` must succeed before writing.
        This parser deliberately rejects many valid Git diff features.
        """
        if not isinstance(patch, str) or not patch.endswith('\n') or '\x00' in patch or '\r' in patch:
            raise UnsafePatch('Patch must be newline-terminated UTF-8 text')
        try:
            encoded = patch.encode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise UnsafePatch('Invalid UTF-8 patch') from exc
        if len(encoded) > self.policy.max_bytes:
            raise UnsafePatch('Patch exceeds byte budget')
        lines = patch.splitlines()
        paths: list[str] = []
        added = removed = 0
        section = ''
        old_seen = new_seen = hunk_seen = False
        old_path = ''
        hunk_remaining_old = hunk_remaining_new = 0
        for line in lines:
            match = _DIFF.fullmatch(line)
            if match and (not section or (old_seen and new_seen and hunk_seen and hunk_remaining_old == 0 and hunk_remaining_new == 0)):
                if match[1] != match[2]:
                    raise UnsafePatch('Rename/copy is not supported')
                path = match[1]
                self.policy.check_path(workspace, path)
                if path in paths:
                    raise UnsafePatch('Duplicate file in patch')
                paths.append(path)
                if len(paths) > self.policy.max_files:
                    raise UnsafePatch('Too many changed files')
                section = 'header'
                old_seen = new_seen = hunk_seen = False
                old_path = ''
                continue
            if not section:
                raise UnsafePatch('Expected diff --git header')
            if line.startswith('diff --git '):
                raise UnsafePatch('Malformed diff boundary or unbalanced hunk')
            if section == 'header':
                if line.startswith('index '):
                    if not re.fullmatch(r'index [0-9a-f]{6,64}\.\.[0-9a-f]{6,64}(?: 100644)?', line):
                        raise UnsafePatch('Unsupported index or file mode')
                elif line == 'new file mode 100644' and not old_seen:
                    pass
                elif line.startswith('--- ') and not old_seen:
                    old_path = line[4:]
                    if old_path not in (f'a/{paths[-1]}', '/dev/null'):
                        raise UnsafePatch('Invalid old file header')
                    old_seen = True
                elif line == f'+++ b/{paths[-1]}' and old_seen and not new_seen:
                    new_seen = True
                    section = 'hunk-header'
                else:
                    raise UnsafePatch('Unsupported patch metadata')
            elif section in ('hunk-header', 'hunk'):
                h = _HUNK.fullmatch(line)
                if h and hunk_remaining_old == 0 and hunk_remaining_new == 0:
                    hunk_remaining_old = int(h[2] if h[2] is not None else '1')
                    hunk_remaining_new = int(h[4] if h[4] is not None else '1')
                    hunk_seen = True
                    section = 'hunk'
                elif section == 'hunk' and (hunk_remaining_old > 0 or hunk_remaining_new > 0):
                    if line.startswith(' '):
                        hunk_remaining_old -= 1
                        hunk_remaining_new -= 1
                    elif line.startswith('+'):
                        hunk_remaining_new -= 1
                        added += 1
                    elif line.startswith('-'):
                        hunk_remaining_old -= 1
                        removed += 1
                    else:
                        raise UnsafePatch('Malformed hunk line')
                    if hunk_remaining_old < 0 or hunk_remaining_new < 0:
                        raise UnsafePatch('Hunk has excess lines')
                elif line == r'\ No newline at end of file' and hunk_seen:
                    pass
                else:
                    raise UnsafePatch('Malformed or incomplete hunk')
            if added + removed > self.policy.max_changed_lines:
                raise UnsafePatch('Patch exceeds changed-line budget')
        if not paths or not old_seen or not new_seen or not hunk_seen or hunk_remaining_old or hunk_remaining_new:
            raise UnsafePatch('Incomplete Git patch')
        return PatchInspection(tuple(paths), added, removed, hashlib.sha256(encoded).hexdigest(), len(encoded))

    @staticmethod
    def _git_prefix(workspace: Path, git_dir: Path | None) -> list[str]:
        """Git options for a worktree. With ``git_dir`` Git ignores ``<worktree>/.git`` and its config."""
        prefix = ['git', '-C', str(workspace)]
        if git_dir is not None:
            prefix += ['--git-dir', str(git_dir), '--work-tree', str(workspace), '-c', 'core.fsmonitor=false']
        return prefix + ['-c', 'core.hooksPath=/dev/null']

    def check_git_modes(self, workspace: Path, paths: tuple[str, ...], git_dir: Path | None = None) -> None:
        """Block Git symlinks/submodules and non-regular tracked file modes."""
        for path in paths:
            proc = subprocess.run([*self._git_prefix(workspace, git_dir), 'ls-files', '--stage', '--', path],
                                  text=True, capture_output=True, check=True, timeout=15)
            for line in proc.stdout.splitlines():
                mode = line.split(' ', 1)[0]
                if mode != '100644':
                    raise UnsafePatch('Tracked target is not a regular non-executable file')

    def apply(self, patch: str, workspace: Path, git_dir: Path | None = None) -> PatchInspection:
        """Apply a validated patch to one already-created disposable worktree.

        Calls Git only with fixed executable arguments and stdin patch bytes;
        never interpolates model-controlled text into a shell command.
        """
        inspection = self.inspect(patch, workspace)
        self.check_git_modes(workspace, inspection.paths, git_dir)
        prefix = self._git_prefix(workspace, git_dir)
        command = [*prefix, 'apply', '--whitespace=error', '--']
        data = patch.encode('utf-8')
        check = subprocess.run([*prefix, 'apply', '--check', '--whitespace=error', '--'], input=data,
                               capture_output=True, timeout=20)
        if check.returncode != 0:
            raise UnsafePatch('Git rejected patch preflight')
        # No partial application is expected after --check; failure still
        # quarantines the whole worktree for independent reconciliation.
        result = subprocess.run(command, input=data, capture_output=True, timeout=20)
        if result.returncode != 0:
            raise UnsafePatch('Git failed to apply patch')
        return inspection
