"""Bounded patch execution in disposable worktrees with independent receipts.

A patch proposer may be a language model, human-supplied patch, or mock; none
of them can authorize writes, weaken the patch policy, or certify completion.
Only an explicit caller approval enables writes, and untrusted tests require
an external sandbox backend. The original checkout is never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from .code_models import CodingOutcome, PatchInspection, PatchProposal, TestOutcome, Workspace
from .code_acceptance import TaskContract
from .code_ports import CodingVerifier, IsolatedTestRunner, PatchProposer
from .code_patches import PatchPolicy, PatchValidator, UnsafePatch
from .code_workspace import GitWorktreeManager, WorkspaceError, _git, worktree_git_dir
from .code_sandbox import DockerTestRunner
from .identity import load_project
from .storage import SQLiteRepository


class PatchFileProposer(PatchProposer):
    """Use an operator-supplied patch exactly as supplied, without model calls."""

    def __init__(self, patch: str):
        self.patch = patch

    def propose(self, task: str, source_files: dict[str, str]) -> PatchProposal:
        """Return the candidate patch; the host still validates every byte."""
        return PatchProposal(self.patch, 'Operator supplied local patch')


class CodeReceiptVerifier(CodingVerifier):
    """Reconcile host-side Git/test events, rejecting fake worker evidence.

    A passing result certifies only that the patch was applied as authorized,
    the resulting code did not change unexpectedly during tests, and all real
    isolated tests passed. It does *not* certify task semantics or deployment.
    """

    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def verify(self, run_id: str, inspection: PatchInspection, diff_sha256: str,
               tests: tuple[TestOutcome, ...]) -> tuple[bool, str]:
        """Verify immutable SHA-256 receipts and real isolation proof labels."""
        events = self.repository.events(run_id)
        started = [e for e in events if e['event'] == 'code_started']
        patch = [e['payload'] for e in events if e['event'] == 'code_patch_applied']
        integrity = [e['payload'] for e in events if e['event'] == 'code_post_test_integrity']
        receipts = [e['payload'] for e in events if e['event'] == 'code_test_receipt']
        if len(started) != 1 or len(patch) != 1 or len(integrity) != 1:
            return False, 'Incomplete or duplicate host evidence'
        if (patch[0].get('sha256') != inspection.sha256 or
                patch[0].get('diff_sha256') != diff_sha256 or
                patch[0].get('paths') != list(inspection.paths)):
            return False, 'Host patch receipt mismatch'
        if not integrity[0].get('unchanged') or integrity[0].get('diff_sha256') != diff_sha256:
            return False, 'Workspace changed unexpectedly during verification'
        if len(receipts) != len(tests) or not tests:
            return False, 'Missing test receipts'
        if started[0]['payload'].get('requires_red_test'):
            red = [e['payload'] for e in events if e['event'] == 'code_prepatch_test_receipt']
            if (len(red) != 1 or red[0].get('kind') != 'docker' or
                    type(red[0].get('exit_code')) is not int or red[0]['exit_code'] == 0 or
                    red[0].get('timed_out') or red[0].get('output_truncated') or
                    not isinstance(red[0].get('receipt_sha256'), str) or
                    len(red[0]['receipt_sha256']) != 64 or
                    red[0].get('command_sha256') != receipts[0].get('command_sha256')):
                return False, 'Missing independently receipted failing baseline regression'
        for record, test in zip(receipts, tests):
            if record.get('receipt_sha256') != _test_receipt_hash(test):
                return False, 'Test receipt mismatch'
            if test.kind != 'docker':
                return False, 'Mock/static checks cannot certify isolated real tests'
            if test.timed_out or test.exit_code != 0 or test.output_truncated:
                return False, 'An isolated test failed, timed out or truncated output'
        return True, 'All independently receipted isolated tests passed; semantic task approval remains outstanding'


def _test_receipt_hash(receipt: TestOutcome) -> str:
    """Bind all test fields to one canonical host-computed SHA-256."""
    return hashlib.sha256(json.dumps(receipt.to_dict(), sort_keys=True).encode()).hexdigest()


def _validated_source_files(root: Path, targets: tuple[str, ...], policy: PatchPolicy) -> dict[str, str]:
    """Supply only caller-selected, UTF-8, nonsecret regular files to a proposer."""
    result: dict[str, str] = {}
    if len(targets) > 8:
        raise ValueError('At most eight model source files are permitted')
    for relative in targets:
        policy.check_path(root, relative)
        source = root / relative
        if not source.is_file():
            raise ValueError('Target source file does not exist')
        if source.stat().st_size > 16_000:
            raise ValueError('Source file exceeds the context budget')
        result[relative] = source.read_text(encoding='utf-8')
    if sum(len(x.encode('utf-8')) for x in result.values()) > 48_000:
        raise ValueError('Selected source context is too large')
    return result


def _changed_diff(workspace: Workspace, inspection: PatchInspection) -> tuple[str, str]:
    """Capture Git's canonical tracked/new-file diff and enforce exact scope."""
    root = Path(workspace.path)
    # The container could write to this worktree, so never let Git read the `.git` entry inside it.
    git_dir = worktree_git_dir(workspace)
    status = _git(root, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching', git_dir=git_dir)
    changed: set[str] = set()
    new: list[str] = []
    for line in status.splitlines():
        if len(line) < 4 or line[:2] == '!!' or line[3:] not in inspection.paths:
            raise UnsafePatch('Unexpected file change, ignored output or untracked artifact')
        if line[:2] == '??':
            new.append(line[3:])
        changed.add(line[3:])
    if changed != set(inspection.paths):
        raise UnsafePatch('Changed files do not match approved patch scope')
    if new:
        _git(root, 'add', '-N', '--', *new, git_dir=git_dir)
    diff = _git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD', '--', *inspection.paths,
                 git_dir=git_dir)
    if not diff:
        raise UnsafePatch('Patch did not change any source')
    return hashlib.sha256(diff.encode('utf-8')).hexdigest(), diff


def _save_patch_artifact(state_root: Path, run_id: str, patch: str) -> str:
    """Persist patch bytes in a local 0600 artifact, never in event payloads."""
    root = state_root / 'code-artifacts'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise ValueError('Symlinked artifact directory')
    path = root / (run_id + '.patch')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(patch.encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return str(path)


def run_coding_task(*, project_path: Path, state_root: Path, repository: SQLiteRepository,
                    task: str, proposer: PatchProposer, runner: IsolatedTestRunner,
                    test_commands: tuple[tuple[str, ...], ...], approved_writes: bool = False,
                    patch_policy: PatchPolicy | None = None,
                    source_targets: tuple[str, ...] = (), timeout_seconds: int = 120,
                    verifier: CodingVerifier | None = None,
                    contract: TaskContract | None = None, run_id: str | None = None,
                    require_red_test: bool = False) -> CodingOutcome:
    """Execute one bounded patch proposal in an isolated worktree.

    Procedure: authenticated project → explicit write consent → clean commit →
    disposable worktree → strict patch preflight → apply → artifact and receipts
    → isolated tests → unchanged-diff check → independent verification → cleanup.

    Raises:
        PermissionError: Writes were not explicitly authorized.
        UnsafePatch: The patch violates host safety or escapes its approved scope.
        WorkspaceError: Git isolation or reconciliation failed.

    The caller must configure a separate safe sandbox for untrusted tests.
    This API never merges, commits, pushes, or changes the original checkout.
    All failure paths append metadata-only events before re-raising.
    """
    if type(require_red_test) is not bool:
        raise ValueError('require_red_test must be a boolean')
    if require_red_test and contract is None:
        raise ValueError('A failing baseline requires an operator-authored contract')
    if not approved_writes:
        raise PermissionError('Each coding run requires explicit write approval')
    if not 1 <= len(task) <= 8_000 or not 1 <= len(test_commands) <= 5:
        raise ValueError('Invalid task or test count')
    if not isinstance(runner, IsolatedTestRunner) or not isinstance(proposer, PatchProposer):
        raise TypeError('Workers must implement the published override contracts')
    for command in test_commands:
        if not command or any(type(arg) is not str or not arg for arg in command):
            raise ValueError('Test commands must contain nonempty arguments')
    if not 1 <= timeout_seconds <= 600:
        raise ValueError('Invalid test timeout')
    project = load_project(project_path, repository)
    root = project_path.resolve(strict=True)
    policy = patch_policy or PatchPolicy()
    if contract is not None:
        if not isinstance(contract, TaskContract):
            raise TypeError('Acceptance contract must use the published immutable schema')
        contract.preflight(root, policy, test_commands)
    selected = _validated_source_files(root, source_targets, policy)
    manager = GitWorktreeManager(state_root)
    run_id = run_id or f'code_{uuid.uuid4().hex}'
    if not re.fullmatch(r'code_[0-9a-f]{32}', run_id):
        raise ValueError('Invalid caller-supplied coding run ID')
    if repository.events(run_id):
        raise ValueError('Coding run has already started; refuse ambiguous replay')
    repository.record_event(run_id, 'code_started', {
        'project_id': project.project_id, 'task_sha256': hashlib.sha256(task.encode()).hexdigest(),
        'task_chars': len(task), 'test_count': len(test_commands), 'write_approved': True,
        'source_target_count': len(selected),
        'contract_sha256': contract.sha256 if contract is not None else '',
        'requires_red_test': require_red_test})
    workspace: Workspace | None = None
    artifact = ''
    outcome: CodingOutcome | None = None
    try:
        workspace = manager.create(root, run_id)
        repository.record_event(run_id, 'code_worktree_created', {
            'base_commit': workspace.base_commit, 'worktree_run_id': run_id})
        if require_red_test:
            # The FIRST approved contract command is the bug-regression test.
            # It must demonstrably fail on the pristine base (not time out).
            # Mock tests never satisfy this authorization gate.
            baseline = runner.run(workspace, contract.test_commands[0], timeout_seconds)
            if not isinstance(baseline, TestOutcome):
                raise TypeError('Baseline sandbox returned an invalid test receipt')
            repository.record_event(run_id, 'code_prepatch_test_receipt', {
                'receipt_sha256': _test_receipt_hash(baseline),
                'kind': baseline.kind, 'exit_code': baseline.exit_code,
                'timed_out': baseline.timed_out, 'output_truncated': baseline.output_truncated,
                'command_sha256': hashlib.sha256(json.dumps(contract.test_commands[0]).encode()).hexdigest()})
            if (type(runner) is not DockerTestRunner or baseline.kind != 'docker' or
                    baseline.timed_out or baseline.output_truncated or
                    type(baseline.exit_code) is not int or baseline.exit_code == 0):
                raise RuntimeError('Red test not independently established on original source')
        proposal = proposer.propose(task, selected)
        if not isinstance(proposal, PatchProposal):
            raise TypeError('Patch proposer returned an invalid result')
        validator = PatchValidator(policy)
        inspection = validator.apply(proposal.patch, Path(workspace.path), git_dir=worktree_git_dir(workspace))
        digest, _ = _changed_diff(workspace, inspection)
        if contract is not None and (not set(inspection.paths) <= set(contract.allowed_paths) or
                                     not set(contract.required_paths) <= set(inspection.paths)):
            raise UnsafePatch('Patch violates approved contract path scope')
        artifact = _save_patch_artifact(state_root, run_id, proposal.patch)
        repository.record_event(run_id, 'code_patch_applied', {
            'sha256': inspection.sha256, 'size_bytes': inspection.size_bytes,
            'paths': list(inspection.paths), 'added_lines': inspection.added_lines,
            'removed_lines': inspection.removed_lines, 'diff_sha256': digest})
        receipts: list[TestOutcome] = []
        for command in test_commands:
            receipt = runner.run(workspace, command, timeout_seconds)
            if not isinstance(receipt, TestOutcome):
                raise TypeError('Sandbox returned an invalid test receipt')
            receipts.append(receipt)
            repository.record_event(run_id, 'code_test_receipt', {
                'receipt_sha256': _test_receipt_hash(receipt), 'kind': receipt.kind,
                'exit_code': receipt.exit_code, 'timed_out': receipt.timed_out,
                'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest()})
            if receipt.exit_code != 0 or receipt.timed_out or receipt.output_truncated:
                break
        try:
            after_digest, _ = _changed_diff(workspace, inspection)
            unchanged = (after_digest == digest)
        except (UnsafePatch, WorkspaceError):
            after_digest, unchanged = '', False
        repository.record_event(run_id, 'code_post_test_integrity', {
            'diff_sha256': after_digest, 'unchanged': unchanged})
        contract_passed = False
        contract_reason = 'no_explicit_task_contract'
        if contract is not None:
            if unchanged:
                contract_passed, contract_reason = contract.evaluate(workspace, inspection)
            else:
                contract_reason = 'workspace_integrity_failed'
            repository.record_event(run_id, 'code_contract_check', {
                'contract_sha256': contract.sha256, 'passed': contract_passed,
                'reason': contract_reason})
        checker = verifier or CodeReceiptVerifier(repository)
        verified, reason = checker.verify(run_id, inspection, digest, tuple(receipts))
        # Host verification cannot be overridden into success without isolated
        # real tests and matching hashes, even if a custom checker lies.
        host_verified, host_reason = CodeReceiptVerifier(repository).verify(run_id, inspection, digest, tuple(receipts))
        if not host_verified:
            verified, reason = False, host_reason
        if type(runner) is not DockerTestRunner:
            verified, reason = False, 'Only the built-in restricted Docker runner can certify real tests'
        contract_verified = bool(verified and contract is not None and contract_passed)
        if contract is not None and not contract_passed:
            verified, reason = False, 'Acceptance contract failed: ' + contract_reason
        repository.record_event(run_id, 'code_verification', {
            'acceptance_verified': bool(verified), 'task_completed': False,
            'reason_sha256': hashlib.sha256(reason.encode()).hexdigest(),
            'contract_verified': contract_verified})
        outcome = CodingOutcome(run_id, workspace.base_commit, inspection.sha256, digest,
                                inspection.paths, artifact, bool(verified), False, reason, tuple(receipts),
                                contract_verified, contract.sha256 if contract is not None else '')
    except BaseException as exc:
        repository.record_event(run_id, 'code_failed', {'type': type(exc).__name__})
        exc.add_note(f'DecisionBound coding run_id={run_id}; inspect with dbound trace {run_id}')
        raise
    finally:
        if workspace is not None:
            try:
                manager.cleanup(run_id)
                repository.record_event(run_id, 'code_workspace_cleaned', {'ok': True})
            except BaseException as cleanup_error:
                repository.record_event(run_id, 'code_workspace_cleaned', {
                    'ok': False, 'type': type(cleanup_error).__name__})
                # Cannot certify a run that left a managed worktree in a
                # potentially modified, uncertain state.
                if outcome is not None:
                    outcome = CodingOutcome(outcome.run_id, outcome.base_commit, outcome.patch_sha256,
                                            outcome.diff_sha256, outcome.changed_paths, outcome.patch_artifact,
                                            False, False, 'Workspace cleanup requires operator reconciliation',
                                            outcome.tests, False, outcome.contract_sha256)
    if outcome is None:
        raise RuntimeError('Coding run produced no final outcome')
    repository.record_event(run_id, 'code_finished', {'acceptance_verified': outcome.acceptance_verified,
                                                       'contract_verified': outcome.contract_verified,
                                                       'contract_sha256': outcome.contract_sha256,
                                                       'task_completed': False})
    return outcome
