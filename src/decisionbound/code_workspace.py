"""Durable, disposable Git worktrees for bounded coding tasks.

All mutations are made in detached worktrees under the user's DecisionBound
state directory. This module neither merges branches nor modifies the original
checkout's tracked files. Interrupted runs leave records for manual cleanup.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from .code_models import Workspace

_RUN_ID = re.compile(r'^code_[a-f0-9]{32}$')


class WorkspaceError(RuntimeError):
    """A project cannot be safely isolated or an owned worktree cannot be cleaned."""


class GitCommandError(WorkspaceError):
    """The Git executable was missing, timed out or exited with an error.

    This separates "Git could not answer" from "Git answered and the checkout
    is not acceptable". The message never carries paths or Git's stderr.
    """


def _git_env() -> dict[str, str]:
    """Disable inherited Git/SSH configuration overrides during source checkout.

    A user's environment can contain `GIT_CONFIG_COUNT` and related options
    that insert executable filters even when repo-local `git config` appears
    harmless. Keep normal OS variables such as PATH, but exclude all inherited
    Git- and SSH-specific overrides before invoking a trusted local Git binary.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_', 'SSH_'))}
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_SYSTEM': os.devnull})
    return env


_LINE_ENDING_VALUES = {'core.autocrlf': ('true', 'false', 'input'), 'core.eol': ('lf', 'crlf', 'native')}


def _line_ending_options(root: Path, git_dir: Path | None = None) -> list[str]:
    """Forward the user's effective line-ending settings to isolated Git calls.

    `_git_env` hides system and global configuration, but Git for Windows sets
    ``core.autocrlf=true`` in its system file. Without that setting a clean
    CRLF checkout looks modified and is refused. Only these two
    non-executable keys are read, and only allow-listed values are forwarded.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_', 'SSH_'))}
    env['GIT_TERMINAL_PROMPT'] = '0'
    try:
        result = subprocess.run(['git', '-C', str(root), *_trusted_location(root, git_dir),
                                 'config', '--get-regexp', r'^core\.(autocrlf|eol)$'],
                                capture_output=True, text=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    settings: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.strip().partition(' ')
        key, value = key.lower(), value.strip().lower()
        if value in _LINE_ENDING_VALUES.get(key, ()):
            settings[key] = value  # later entries have higher precedence
    return [item for key, value in sorted(settings.items()) for item in ('-c', f'{key}={value}')]


def _trusted_location(worktree: Path, git_dir: Path | None) -> list[str]:
    """Git options that ignore ``<worktree>/.git`` and the config it would select.

    A container that ran with the worktree mounted read/write can replace the ``.git`` file with a
    directory of its own. Git would then read that directory's config and may run programs it names
    (for example ``core.fsmonitor``). A trusted git directory outside the mount, with the options
    below, makes Git ignore the planted one.
    """
    if git_dir is None:
        return []
    return ['--git-dir', str(git_dir), '--work-tree', str(worktree), '--literal-pathspecs',
            '-c', 'core.fsmonitor=false']


def worktree_git_dir(workspace: Workspace) -> Path:
    """Find the Git metadata directory of a managed worktree without reading anything the worktree holds.

    The answer comes only from the source repository's own ``worktrees/*/gitdir`` files, which the
    container cannot reach. Exactly one entry must point back at this worktree.
    """
    try:
        worktree = Path(workspace.path)
        repository = Path(workspace.repository_root)
        common = Path(_git(repository, 'rev-parse', '--git-common-dir'))
        common = (common if common.is_absolute() else repository / common).resolve(strict=True)
        wanted = os.path.normcase(str((worktree.resolve(strict=True) / '.git')))
        found = []
        for entry in sorted((common / 'worktrees').glob('*')):
            try:
                pointer = (entry / 'gitdir').read_text(encoding='utf-8').strip()
            except OSError:
                continue
            # A pointer may be relative to its own directory (worktree.useRelativePaths).
            if os.path.normcase(str((entry / pointer).resolve())) == wanted:
                found.append(entry.resolve(strict=True))
    except OSError as exc:
        raise WorkspaceError('Worktree or source repository is not available') from exc
    if len(found) != 1:
        raise WorkspaceError('Git does not list exactly one metadata directory for this worktree')
    return found[0]


def git_entry_is_original(workspace: Workspace, git_dir: Path) -> bool:
    """True only while ``<worktree>/.git`` is still the regular pointer file Git wrote.

    Test code can replace it with a directory or a symlink. Nothing runs from it once the host passes
    ``--git-dir``, but a replaced entry means the worktree was tampered with and the run is not trusted.
    """
    entry = Path(workspace.path) / '.git'
    try:
        if entry.is_symlink() or not entry.is_file():
            return False
        with entry.open('rb') as handle:
            text = handle.read(4096).decode('utf-8').strip()  # bounded: the container controls this file
        if not text.startswith('gitdir:'):
            return False
        pointer = text[len('gitdir:'):].strip()
        return (Path(workspace.path) / pointer).resolve() == git_dir.resolve()
    except (OSError, UnicodeError):
        return False


def _git(root: Path, *args: str, timeout: int = 30, git_dir: Path | None = None) -> str:
    """Call the Git executable with a fixed argument vector, never a shell.

    Pass ``git_dir`` (from :func:`worktree_git_dir`) for any worktree that untrusted code has touched.
    """
    env = _git_env()
    try:
        result = subprocess.run(['git', '-C', str(root), *_trusted_location(root, git_dir),
                                 '-c', 'core.hooksPath=/dev/null',
                                 *_line_ending_options(root, git_dir), *args],
                                capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError('Git command unavailable or timed out') from exc
    if result.returncode:
        raise GitCommandError(f'Git operation failed: {args[0]}')
    return result.stdout.rstrip('\r\n')


class GitWorktreeManager:
    """Own isolated worktrees and retain resumable cleanup metadata.

    Args:
        state_root: Private writable root for records and temporary worktrees.

    Safety:
        Only clean Git repositories are accepted. No shell evaluation, remote
        Git fetch, commit, push, merge or repository hook is performed. Custom
        configured Git filters are refused to avoid checkout-time executables.
        A Git worktree is *not* a process sandbox: untrusted test code must run
        through a separate isolation backend.
    """

    def __init__(self, state_root: Path):
        requested = state_root.expanduser().absolute()
        if requested.is_symlink():
            raise WorkspaceError('Refusing symlinked state root')
        self.root = requested.resolve()
        self.worktrees = self.root / 'code-worktrees'
        self.records = self.root / 'code-records'
        for path in (self.worktrees, self.records):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise WorkspaceError('Refusing symlinked workspace directory')

    @staticmethod
    def check_source(repository_root: Path) -> tuple[Path, str]:
        """Validate a clean Git root and return the immutable base commit."""
        root = repository_root.resolve(strict=True)
        if not root.is_dir() or _git(root, 'rev-parse', '--is-bare-repository') != 'false':
            raise WorkspaceError('A non-bare Git repository is required')
        actual = Path(_git(root, 'rev-parse', '--show-toplevel')).resolve(strict=True)
        if root != actual:
            raise WorkspaceError('Use the Git repository top-level directory')
        try:
            filter_check = subprocess.run(['git', '-C', str(root), 'config', '--local', '--name-only',
                                           '--get-regexp', r'^filter\.'], capture_output=True, text=True,
                                           timeout=15, env=_git_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitCommandError('Git command unavailable or timed out') from exc
        if filter_check.returncode not in (0, 1) or filter_check.stdout.strip():
            raise WorkspaceError('Configured Git filters are not supported in isolated checkout')
        status = _git(root, 'status', '--porcelain', '--untracked-files=normal')
        if any(line and not (line.startswith('?? ') and line[3:] in ('.decisionbound/', '.decisionbound'))
               for line in status.splitlines()):
            raise WorkspaceError('Source checkout has unrelated changes or untracked files')
        return root, _git(root, 'rev-parse', '--verify', 'HEAD')

    def _record_file(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid coding run ID')
        return self.records / (run_id + '.json')

    def create(self, repository_root: Path, run_id: str) -> Workspace:
        """Create one detached worktree and persist a recovery record."""
        record_file = self._record_file(run_id)
        root, sha = self.check_source(repository_root)
        target = self.worktrees / run_id
        if target.exists() or target.is_symlink() or record_file.exists():
            raise WorkspaceError('Run workspace already exists; reconcile instead of retrying')
        _git(root, 'worktree', 'add', '--detach', str(target), sha)
        workspace = Workspace(str(target), str(root), sha, run_id)
        payload = {'run_id': run_id, 'repository_root': str(root), 'worktree': str(target),
                   'base_commit': sha, 'closed': False}
        temporary = record_file.with_suffix('.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as handle:
                os.chmod(temporary, 0o600)
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, record_file)
        except BaseException:
            temporary.unlink(missing_ok=True)
            _git(root, 'worktree', 'remove', '--force', str(target))
            raise
        return workspace

    def reconcile(self, run_id: str) -> Workspace | None:
        """Return an interrupted run's workspace without creating new state."""
        record = self._record_file(run_id)
        if not record.exists() or record.is_symlink():
            return None
        data = json.loads(record.read_text(encoding='utf-8'))
        if data.get('closed'):
            return None
        target = self.worktrees / run_id
        if data.get('worktree') != str(target) or target.is_symlink():
            raise WorkspaceError('Workspace record does not match managed directory')
        if not target.is_dir():
            raise WorkspaceError('Managed worktree missing; operator reconciliation required')
        return Workspace(str(target), data['repository_root'], data['base_commit'], run_id)

    def cleanup(self, run_id: str) -> bool:
        """Remove only a registered, managed worktree and mark its record closed.

        Cleanup is idempotent. If validation fails, leave the workspace intact
        for manual investigation rather than recursively deleting arbitrary
        paths. This may remove uncommitted edits in *the isolated worktree*.
        """
        workspace = self.reconcile(run_id)
        if workspace is None:
            return False
        root = Path(workspace.repository_root).resolve(strict=True)
        target = Path(workspace.path)
        if target.parent != self.worktrees or target.is_symlink():
            raise WorkspaceError('Refusing unmanaged cleanup target')
        listed = _git(root, 'worktree', 'list', '--porcelain')
        # Git prints forward slashes on Windows, so compare normalized paths.
        registered = {os.path.normcase(str(Path(line[len('worktree '):]).resolve()))
                      for line in listed.splitlines() if line.startswith('worktree ')}
        if os.path.normcase(str(target.resolve())) not in registered:
            raise WorkspaceError('Git does not recognize this managed worktree')
        _git(root, 'worktree', 'remove', '--force', str(target))
        record = self._record_file(run_id)
        payload = json.loads(record.read_text(encoding='utf-8'))
        payload['closed'] = True
        record.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')
        os.chmod(record, 0o600)
        return True

    def pending(self) -> list[str]:
        """List runs with durable cleanup records not yet marked closed."""
        out = []
        for path in sorted(self.records.glob('code_*.json')):
            if path.is_symlink():
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if not data.get('closed'):
                out.append(str(data['run_id']))
        return out
