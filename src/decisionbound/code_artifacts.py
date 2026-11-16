"""Human-controlled export of local, integrity-checked coding artifacts.

A patch is *never* automatically merged, committed, applied to the original
checkout, published, or pushed by this module. Consumers must explicitly
review and promote it through their own Git workflow.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from .storage import SQLiteRepository

_RUN = re.compile(r'^code_[0-9a-f]{32}$')


class ArtifactVerificationError(RuntimeError):
    """A private patch no longer matches the host's original run receipt."""


def export_coding_patch(*, repository: SQLiteRepository, state_root: Path, run_id: str,
                        destination: Path, acknowledge_unverified: bool = False) -> dict:
    """Safely export a reviewed Git patch with its recorded provenance.

    The operation verifies that the run reached a terminal state, the patch
    receipt is unique, and saved bytes still match its SHA-256 digest. Failed
    or mock runs require an explicit acknowledgment before export. Destination
    creation is exclusive to avoid overwriting any existing user file.
    """
    if not _RUN.fullmatch(run_id):
        raise ValueError('Invalid code run ID')
    events = repository.events(run_id)
    applied = [e['payload'] for e in events if e['event'] == 'code_patch_applied']
    done = [e['payload'] for e in events if e['event'] == 'code_finished']
    if len(applied) != 1 or len(done) != 1:
        raise ArtifactVerificationError('Run is incomplete or has ambiguous receipts')
    verified = done[0].get('acceptance_verified') is True
    if not verified and not acknowledge_unverified:
        raise PermissionError('Unverified patch export requires explicit acknowledgment')
    state = state_root.expanduser().resolve(strict=True)
    source = state / 'code-artifacts' / (run_id + '.patch')
    if source.is_symlink() or not source.is_file():
        raise ArtifactVerificationError('Patch artifact missing or symlinked')
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != applied[0].get('sha256') or len(data) != applied[0].get('size_bytes'):
        raise ArtifactVerificationError('Patch bytes no longer match the recorded evidence')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Export destination already exists')
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError('Destination parent must be an existing regular directory')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {'run_id': run_id, 'path': str(destination), 'sha256': digest,
            'acceptance_verified': verified, 'requires_review': True,
            'task_completed': False}
