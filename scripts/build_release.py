#!/usr/bin/env python3
"""Build reproducible, credential-free source/Pages ZIPs from an audited tree.

This does not sign, publish, or upload anything. Stable archive entry metadata
and a SHA-256 file manifest make accidental content drift visible. Supply a
specific --epoch when doing an official release; default is this alpha's
review date. Never put private user state or runtime credentials in the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import zipfile

from datetime import datetime, timezone
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules",
                 "htmlcov", "build", "site", ".vscode-test"}
EXCLUDED_FILES = {".coverage", ".env", "RELEASE_MANIFEST.json"}
SENSITIVE_NAMES = {"id_rsa", "id_ed25519", "id_ecdsa", "credentials.json", "service-account.json", "local_token"}
PUBLIC_FILES = set(json.loads((ROOT / "PUBLIC_CONTENT.json").read_text())["files"])
SENSITIVE_EXTENSIONS = {".pem", ".p12", ".pfx", ".key", ".kdbx", ".sqlite", ".sqlite3", ".db"}


def _unsafe_release_path(relative: str) -> bool:
    """Reject common key, credential and persisted-state paths as a fail-closed guard.

    This is defense in depth, *not* a replacement for dedicated secret scanning.
    Conventional filenames such as keyring exports must never enter archives.
    """
    path = Path(relative)
    return (path.name.lower() in SENSITIVE_NAMES or
            path.suffix.lower() in SENSITIVE_EXTENSIONS or
            ".decisionbound" in path.parts)


def _eligible(path: Path) -> bool:
    """Allow the reviewed source tree, never state/credentials/caches."""
    rel = path.relative_to(ROOT)
    if rel.parts[0] != "dist" and rel.as_posix() not in PUBLIC_FILES:
        return False
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in rel.parts):
        return False
    if path.name in EXCLUDED_FILES or path.suffix in {".pyc", ".pyo"}:
        return False
    if _unsafe_release_path(rel.as_posix()):
        raise ValueError(f"Release tree contains sensitive or state-like file: {rel.as_posix()}")
    if path.is_symlink():
        raise ValueError("Release cannot contain filesystem symlinks")
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if ".decisionbound" in rel.parts:
        return False
    # Ship only the matching wheel; never preserve an obsolete release wheel.
    if rel.parts[0] == "dist":
        return path.name == f"decisionbound-{VERSION}-py3-none-any.whl"
    return True


def _add(zipper: zipfile.ZipFile, relative: str, data: bytes, timestamp: tuple[int, ...]) -> None:
    """Write deterministic file metadata without retaining local ownership."""
    item = zipfile.ZipInfo(relative, timestamp)
    item.compress_type = zipfile.ZIP_DEFLATED
    item.external_attr = (stat.S_IFREG | 0o644) << 16
    zipper.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def release(output_directory: Path, *, epoch: int) -> dict:
    """Package reviewed source/docs with manifest and verify ZIP CRCs.

    Args:
        output_directory: Existing directory outside the source tree.
        epoch: Stable UTC timestamp for ZIP entry metadata.

    Returns:
        Artifact paths and their SHA-256 hashes, without secret contents.
    """
    output_directory = output_directory.expanduser().resolve()
    if not output_directory.is_dir() or output_directory.is_relative_to(ROOT.resolve()):
        raise ValueError("Release outputs must be placed in an existing directory outside the source tree")
    date = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if not 1980 <= date.year <= 2107:
        raise ValueError("ZIP timestamps must be between 1980 and 2107")
    timestamp = (date.year, date.month, date.day, date.hour, date.minute, date.second)
    from check_publication import audit
    audit(ROOT)
    selected = {}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and _eligible(path):
            selected[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    wheel = f"dist/decisionbound-{VERSION}-py3-none-any.whl"
    if wheel not in selected:
        raise FileNotFoundError(f"Build matching wheel first: {wheel}")
    manifest = {"format": "decisionbound-source-manifest-v1", "version": VERSION,
                "source_count": len(selected), "files": {
                    path: hashlib.sha256(data).hexdigest() for path, data in selected.items()}}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (ROOT / "RELEASE_MANIFEST.json").write_bytes(manifest_bytes)
    selected["RELEASE_MANIFEST.json"] = manifest_bytes
    prefix = f"decisionbound-v{VERSION}/"
    source = output_directory / f"DecisionBound-v{VERSION}-Full-Source.zip"
    with zipfile.ZipFile(source, "w") as z:
        for name, data in sorted(selected.items()):
            _add(z, prefix + name, data, timestamp)
    with zipfile.ZipFile(source) as z:
        assert z.testzip() is None
        for relative, checksum in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + relative)).hexdigest() == checksum

    pages = output_directory / f"DecisionBound-v{VERSION}-GitHub-Pages.zip"
    site = ROOT / "site"
    if not (site / "index.html").is_file():
        raise FileNotFoundError("Build and verify the static Pages site before packaging")
    with zipfile.ZipFile(pages, "w") as z:
        for path in sorted(site.rglob("*")):
            if path.is_file():
                _add(z, path.relative_to(site).as_posix(), path.read_bytes(), timestamp)
    with zipfile.ZipFile(pages) as z:
        assert z.testzip() is None and "index.html" in z.namelist()

    def checksum(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    with zipfile.ZipFile(pages) as archive:
        page_count = len(archive.namelist())
    report = {"version": VERSION, "source_files": len(selected), "pages_files": page_count,
              "source": {"path": str(source), "sha256": checksum(source), "bytes": source.stat().st_size},
              "pages": {"path": str(pages), "sha256": checksum(pages), "bytes": pages.stat().st_size}}
    (output_directory / f"DecisionBound-v{VERSION}-CHECKSUMS.txt").write_text(
        f"{report['source']['sha256']}  {source.name}\n{report['pages']['sha256']}  {pages.name}\n"
        f"{checksum(ROOT / wheel)}  {Path(wheel).name}\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epoch", type=int, default=1794614400, help="Stable ZIP timestamp in UTC epoch seconds")
    args = parser.parse_args()
    print(json.dumps(release(args.output, epoch=args.epoch), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
