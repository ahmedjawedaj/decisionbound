"""Repository identity and provenance utilities.

Project manifests are *discovery hints*, not proofs of authorization. The local
runtime maintains authoritative project records and rejects ID collisions.
"""
from __future__ import annotations
import hashlib
import os
import re
import uuid
from pathlib import Path
import yaml
from .models import Evidence, Project
from .storage import SQLiteRepository

MANIFEST_DIR = ".decisionbound"


def _read_project_manifest(base: Path) -> dict:
    """Parse a bounded, non-symlinked v1 manifest and validate its full shape.

    Malformed repository YAML must not supply a forged ID, arbitrary owner or
    different technology policy to the shared local runtime. Manifests remain
    discovery hints: enrollment and checkout binding supply actual authority.
    """
    directory = base / MANIFEST_DIR
    manifest = directory / "project.yaml"
    if directory.is_symlink() or manifest.is_symlink() or not manifest.is_file():
        raise FileNotFoundError('Project manifest missing or symlinked')
    if not manifest.resolve(strict=True).is_relative_to(base):
        raise ValueError('Project manifest escapes repository root')
    if manifest.stat().st_size > 8192:
        raise ValueError('Project manifest exceeds the size limit')
    data = yaml.safe_load(manifest.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or set(data) !=
        {"schema_version", "project_id", "name", "technologies", "owner"} or
        data.get('schema_version') != 1 or
        not isinstance(data.get('project_id'), str) or
        not re.fullmatch(r'prj_[a-f0-9]{32}', data['project_id']) or
        not isinstance(data.get('name'), str) or not 1 <= len(data['name']) <= 150 or
        not isinstance(data.get('owner'), str) or not 1 <= len(data['owner']) <= 150 or
        not isinstance(data.get('technologies'), list) or len(data['technologies']) > 32 or
        any(not isinstance(tag, str) or not 1 <= len(tag) <= 80 for tag in data['technologies'])):
        raise ValueError('Invalid versioned project manifest')
    return data


def safe_relative_path(root: Path, relative: str) -> Path:
    """Resolve a user-specified repository file without escaping the root.

    Raises:
        ValueError: An absolute, parent-traversal or symlink escape is present.
    """
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Paths must be relative and cannot traverse parents")
    base = root.resolve(strict=True)
    target = (base / path).resolve(strict=True)
    if not target.is_relative_to(base):
        raise ValueError("Path escapes repository")
    if not target.is_file():
        raise ValueError("Evidence must be a regular file")
    return target


def hash_evidence(root: Path, relative: str, kind: str = "adr") -> Evidence:
    """Create an immutable content fingerprint without uploading file contents."""
    path = safe_relative_path(root, relative)
    return Evidence(relative, hashlib.sha256(path.read_bytes()).hexdigest(), kind)


def init_project(root: Path, repository: SQLiteRepository, name: str | None = None,
                 technologies: tuple[str, ...] = (), owner: str = "local") -> Project:
    """Initialize a project manifest once and register it with the local store.

    Existing manifests are read and checked rather than rewritten. A copied
    manifest with a conflicting name/owner cannot silently hijack an ID.
    """
    base = root.resolve(strict=True)
    manifest_dir = base / MANIFEST_DIR
    if manifest_dir.is_symlink():
        raise ValueError("Refusing symlinked project metadata directory")
    manifest = manifest_dir / "project.yaml"
    if manifest.is_symlink():
        raise ValueError("Refusing symlinked project manifest")
    if manifest.exists():
        data = _read_project_manifest(base)
        project = Project(data["project_id"], data["name"], tuple(data["technologies"]), data["owner"])
        repository.add_project(project)
        repository.bind_project_root(project.project_id, base)
        return project
    if not base.is_dir():
        raise ValueError("Root must be an existing directory")
    project = Project(f"prj_{uuid.uuid4().hex}", name or base.name, technologies, owner)
    # Ownership is explicit and local-only. Manifest does not carry credentials.
    manifest_dir.mkdir(mode=0o700, exist_ok=True)
    contents = yaml.safe_dump({"schema_version": 1, "project_id": project.project_id, "name": project.name,
                               "technologies": list(project.technologies), "owner": project.owner}, sort_keys=False)
    # O_EXCL prevents a concurrent initialize from overwriting another manifest.
    fd = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(contents)
    except BaseException:
        manifest.unlink(missing_ok=True)
        raise
    repository.add_project(project)
    repository.bind_project_root(project.project_id, base)
    return project


def load_project(root: Path, repository: SQLiteRepository) -> Project:
    """Load and verify an initialized project against the local registry."""
    base = root.resolve(strict=True)
    data = _read_project_manifest(base)
    project = repository.get_project(str(data.get("project_id", "")))
    if project is None:
        raise PermissionError("Unknown project ID in local registry")
    if (project.name != data.get("name") or project.owner != data.get("owner") or
            tuple(data["technologies"]) != project.technologies):
        raise PermissionError("Manifest conflicts with registered project identity")
    repository.require_project_root(project.project_id, root.resolve(strict=True))
    return project
