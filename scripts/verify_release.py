#!/usr/bin/env python3
"""Independently inspect source/Pages archives before installation or upload.

Prevents malicious ZIP paths, duplicate normalized entries, symlinks, excessive
expanded sizes, missing files and source-manifest hash drift. SHA-256 checksums
*inside* an archive detect accidental corruption, not publisher authenticity;
production distribution still requires externally verified signatures.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


class ArtifactVerificationError(ValueError):
    """A source or documentation archive failed a reproducible safety gate."""


MAX_FILES = 10_000
MAX_FILE_BYTES = 55 * 1024 * 1024
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024


def _entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Validate all ZIP paths and declared sizes before extracting any content."""
    files = archive.infolist()
    if not files or len(files) > MAX_FILES:
        raise ArtifactVerificationError("Invalid archive entry count")
    seen = set()
    expanded = 0
    for entry in files:
        name = entry.filename
        path = PurePosixPath(name)
        if (not name or name.startswith('/') or '\\' in name or '\x00' in name or
            path.as_posix() != name.rstrip('/') or
            any(part in ('..', '.', '') for part in name.rstrip('/').split('/')) or
            ':' in name.split('/')[0]):
            raise ArtifactVerificationError("Unsafe archive entry path")
        identity = name.rstrip('/').casefold()
        if identity in seen:
            raise ArtifactVerificationError("Duplicate case-insensitive archive entry")
        seen.add(identity)
        file_type = stat.S_IFMT(entry.external_attr >> 16)
        if file_type == stat.S_IFLNK:
            raise ArtifactVerificationError("Symlink entry is forbidden")
        if entry.is_dir():
            continue
        if entry.file_size > MAX_FILE_BYTES:
            raise ArtifactVerificationError("File exceeds safe expanded-size limit")
        expanded += entry.file_size
        if expanded > MAX_ARCHIVE_BYTES:
            raise ArtifactVerificationError("Archive exceeds safe total expanded-size limit")
        if entry.file_size > 64 * 1024 and entry.compress_size < entry.file_size / 200:
            raise ArtifactVerificationError("Unusually high decompression ratio")
    return [entry for entry in files if not entry.is_dir()]


def _digest(archive: zipfile.ZipFile, name: str) -> str:
    """Hash a ZIP member incrementally without extracting to the filesystem."""
    sha = hashlib.sha256()
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def verify_source_archive(path: str | Path) -> dict:
    """Check canonical source layout and every SHA-256 value in its manifest."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = _entries(archive)
            names = {entry.filename for entry in entries}
            manifests = [n for n in names if n.endswith('/RELEASE_MANIFEST.json')]
            if len(manifests) != 1:
                raise ArtifactVerificationError("Exactly one top-level source manifest required")
            manifest_name = manifests[0]
            prefix = manifest_name[:-len('RELEASE_MANIFEST.json')]
            match = re.fullmatch(r'decisionbound-v(\d+\.\d+\.\d+(?:a\d+|b\d+|rc\d+)?)/', prefix)
            if not match:
                raise ArtifactVerificationError("Invalid source archive version prefix")
            manifest = json.loads(archive.read(manifest_name))
            version = match.group(1)
            if (manifest.get('format') != 'decisionbound-source-manifest-v1' or
                manifest.get('version') != version or not isinstance(manifest.get('files'), dict)):
                raise ArtifactVerificationError("Invalid source manifest version or format")
            declared = manifest['files']
            if len(declared) != manifest.get('source_count'):
                raise ArtifactVerificationError("Source manifest file-count mismatch")
            expected = {prefix + rel for rel in declared} | {manifest_name}
            if expected != names:
                raise ArtifactVerificationError("Archive entries differ from reviewed manifest")
            required = {'README.md', 'pyproject.toml', 'AGENTS.md', 'SECURITY.md',
                        f'dist/decisionbound-{version}-py3-none-any.whl'}
            if not required <= set(declared):
                raise ArtifactVerificationError("Required package or policy files missing")
            for relative, checksum in declared.items():
                if not re.fullmatch('[a-f0-9]{64}', str(checksum)):
                    raise ArtifactVerificationError("Invalid manifest digest")
                if _digest(archive, prefix + relative) != checksum:
                    raise ArtifactVerificationError("Source archive content checksum mismatch")
            return {'verified': True, 'version': version, 'files': len(entries),
                    'bytes': sum(e.file_size for e in entries), 'signature_verified': False}
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, KeyError) as exc:
        raise ArtifactVerificationError("Source archive unreadable or malformed") from exc


def verify_pages_archive(path: str | Path) -> dict:
    """Validate safe static-site ZIP structure and required entrypoint assets."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = _entries(archive)
            names = {entry.filename for entry in entries}
            if not {'index.html', 'assets/site.css', 'assets/site.js'} <= names:
                raise ArtifactVerificationError("GitHub Pages entrypoint or assets missing")
            if any(name.endswith(('.pyc', '.sqlite3', '.pem', '.p12', '.key')) for name in names):
                raise ArtifactVerificationError("Sensitive file type in Pages ZIP")
            if archive.testzip() is not None:
                raise ArtifactVerificationError("Pages ZIP has a bad CRC")
            return {'verified': True, 'files': len(entries),
                    'bytes': sum(e.file_size for e in entries), 'signature_verified': False}
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArtifactVerificationError("Pages archive unreadable") from exc


def main() -> None:
    """Print machine-readable validation receipts for both public ZIPs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--pages', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'source': verify_source_archive(args.source),
                      'pages': verify_pages_archive(args.pages)}, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
