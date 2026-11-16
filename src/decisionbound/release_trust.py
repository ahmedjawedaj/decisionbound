"""Offline Ed25519 release attestation; does not download or install software.

A fleet client must pin the publisher's independently distributed public key.
A hash recorded inside an unsigned ZIP proves only integrity, not authenticity.
This module verifies an explicit detached signature over canonical manifest
bytes, the expiry window, downgrade policy, and each named artifact's SHA-256.
It does not implement automated rollout, key rotation or secure clock services.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any


class ReleaseTrustError(ValueError):
    """The release signature, policy, metadata or artifact is untrusted."""


def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    """Canonical UTF-8 serialization to sign or verify (plus final newline)."""
    return (json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    """Reject ambiguous duplicate JSON fields before signature validation."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseTrustError('Duplicate signed manifest key')
        result[key] = value
    return result


def _date(value: str) -> datetime:
    """Require an offset-aware RFC3339 timestamp, not implicit local time."""
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ReleaseTrustError('Signed manifest timestamps must use UTC Z')
    try:
        result = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise ReleaseTrustError('Invalid release timestamp') from exc
    if result.tzinfo is None:
        raise ReleaseTrustError('Invalid release timestamp timezone')
    return result


def verify_signed_release(
    manifest_path: str | Path,
    signature_path: str | Path,
    public_key_pem: str | Path,
    artifacts_directory: str | Path,
    *,
    installed_version: str,
    minimum_accepted_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a publisher signature, downgrade/expiry policy, and local artifacts.

    Public keys must arrive through a trusted, out-of-band administrator or
    enterprise policy; fetching a key from the same untrusted release URL is
    NOT authentication. This function never executes any release artifact.

    Args:
        manifest_path: Canonical JSON UTF-8 metadata, maximum 64 KB.
        signature_path: Base64-encoded detached Ed25519 signature.
        public_key_pem: Trusted pinned Ed25519 PEM public key.
        artifacts_directory: Directory containing already-downloaded artifacts.
        installed_version: Currently installed client version; downgrade floor.
        minimum_accepted_version: Optional persistent fleet anti-rollback floor.
        now: UTC clock for explicit tests; production uses UTC wall clock.
    """
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from packaging.version import Version, InvalidVersion
    except ImportError as exc:
        raise ReleaseTrustError("Install the optional release extra: pip install 'decisionbound[release]'") from exc

    manifest_file = Path(manifest_path)
    signature_file = Path(signature_path)
    key_file = Path(public_key_pem)
    directory = Path(artifacts_directory)
    for path, maximum in ((manifest_file, 65536), (signature_file, 256), (key_file, 4096)):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise ReleaseTrustError('Invalid or symlinked release verification input')
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseTrustError('Artifact directory must exist and not be a symlink')

    raw = manifest_file.read_bytes()
    try:
        manifest = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseTrustError('Invalid release manifest encoding') from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        'format', 'channel', 'version', 'issued_at', 'expires_at', 'artifacts'
    } or manifest.get('format') != 'decisionbound-signed-release-v1':
        raise ReleaseTrustError('Unexpected signed release manifest format')
    if raw != canonical_manifest(manifest):
        raise ReleaseTrustError('Release manifest is not canonical JSON')

    try:
        public_key = load_pem_public_key(key_file.read_bytes())
        signature = base64.b64decode(signature_file.read_bytes().strip(), validate=True)
        if not isinstance(public_key, Ed25519PublicKey) or len(signature) != 64:
            raise ReleaseTrustError('Wrong release signing key or signature format')
        public_key.verify(signature, raw)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm, binascii.Error) as exc:
        raise ReleaseTrustError('Release signature is invalid or untrusted') from exc

    if manifest['channel'] not in ('alpha', 'beta', 'stable'):
        raise ReleaseTrustError('Unrecognized signed release channel')
    try:
        offered = Version(manifest['version'])
        installed = Version(installed_version)
        lower_bound = Version(minimum_accepted_version) if minimum_accepted_version else installed
    except (ValueError, TypeError, InvalidVersion) as exc:
        raise ReleaseTrustError('Invalid version or anti-rollback policy') from exc
    if offered < max(installed, lower_bound):
        raise ReleaseTrustError('Signed release violates downgrade policy')
    issued = _date(manifest['issued_at'])
    expires = _date(manifest['expires_at'])
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ReleaseTrustError('Clock must be timezone-aware')
    clock = clock.astimezone(timezone.utc)
    if expires <= issued or not issued <= clock <= expires:
        raise ReleaseTrustError('Release manifest expired or not yet valid')
    if expires - issued > timedelta(days=180):
        raise ReleaseTrustError('Excessive release-manifest validity window')

    artifacts = manifest['artifacts']
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 20:
        raise ReleaseTrustError('Invalid artifact count')
    seen: set[str] = set()
    total_bytes = 0
    for record in artifacts:
        if not isinstance(record, dict) or set(record) != {'name', 'sha256', 'size_bytes'}:
            raise ReleaseTrustError('Invalid artifact entry')
        name = record['name']
        if (not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9._-]+', name) or
            not name.endswith(('.whl', '.zip')) or name.casefold() in seen):
            raise ReleaseTrustError('Unsafe or duplicate artifact filename')
        seen.add(name.casefold())
        if not isinstance(record['size_bytes'], int) or not 0 < record['size_bytes'] <= 200_000_000:
            raise ReleaseTrustError('Invalid signed artifact size')
        if not isinstance(record['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', record['sha256']):
            raise ReleaseTrustError('Invalid signed artifact hash')
        total_bytes += record['size_bytes']
        if total_bytes > 400_000_000:
            raise ReleaseTrustError('Signed release exceeds bounded artifact budget')
        candidate = directory / name
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size != record['size_bytes']:
            raise ReleaseTrustError('Missing, symlinked or wrong-sized signed artifact')
        sha = hashlib.sha256()
        with candidate.open('rb') as reader:
            for chunk in iter(lambda: reader.read(1024*1024), b''):
                sha.update(chunk)
        if sha.hexdigest() != record['sha256']:
            raise ReleaseTrustError('Signed artifact digest mismatch')
    return {'signature_verified': True, 'version': str(offered),
            'channel': manifest['channel'], 'artifact_count': len(artifacts),
            'artifact_bytes': total_bytes, 'automatic_installation_performed': False,
            'publisher_key_rotation_verified': False}
