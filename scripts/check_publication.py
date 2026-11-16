#!/usr/bin/env python3
"""Fail closed when an unreviewed file enters the public distribution."""
from pathlib import Path, PurePosixPath
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'dist', 'build', 'site', 'node_modules', '.vscode-test', 'htmlcov'}
IGNORED_FILES = {'.coverage', 'RELEASE_MANIFEST.json', '.DS_Store'}
PRIVATE_DIRS = {'.agents', '.claude', 'agents', 'skills', 'tasks', 'articles', 'papers', 'research', 'runs', 'private', 'internal'}
PRIVATE_NAMES = re.compile(r'(?:SPRINT.*\.md|.*(?:HANDOFF|DRAFT|BASELINE_VALIDATION).*|CURRENT_STATE\.md|NEXT_SPRINT\.md|PROMPTS\.md|PLANS\.md|GOALS\.md|IMPLEMENTATION_.*|LICENSE_DECISION\.md)', re.I)


def reviewed_paths(root: Path) -> set[str]:
    """Validate a deliberate allowlist; never infer approval from a directory."""
    data = json.loads((root / 'PUBLIC_CONTENT.json').read_text(encoding='utf-8'))
    paths = data.get('files')
    if data.get('format') != 'decisionbound-public-content-v1' or not isinstance(paths, list):
        raise ValueError('Invalid public content manifest')
    result = set()
    casefolded = set()
    for value in paths:
        if not isinstance(value, str):
            raise ValueError('Public paths must be strings')
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or path.as_posix() != value or
                '\\' in value or ':' in value or any(p in ('.', '..', '') for p in value.split('/')) or
                any(p in PRIVATE_DIRS for p in path.parts) or (path.suffix.lower() == ".md" and PRIVATE_NAMES.fullmatch(path.name)) or
                value.casefold() in casefolded):
            raise ValueError('Private, duplicate or unsafe public path: ' + value)
        local = root / value
        if local.is_symlink() or not local.is_file() or not local.resolve().is_relative_to(root.resolve()):
            raise ValueError('Public path is missing, linked or outside root: ' + value)
        result.add(value)
        casefolded.add(value.casefold())
    return result


def audit(root: Path = ROOT) -> set[str]:
    """Reject tracked/unignored source beyond the reviewed allowlist."""
    allowed = reviewed_paths(root)
    if (root / '.git').exists():
        output = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'])
        actual = {p for p in output.decode('utf-8').split('\0') if p}
    else:
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
                  if (p.is_file() or p.is_symlink()) and
                  not any(x in IGNORED_DIRS or x.endswith('.egg-info') for x in p.relative_to(root).parts) and
                  p.name not in IGNORED_FILES and p.suffix not in {'.pyc', '.pyo'}}
    extra = actual - allowed
    if extra:
        raise ValueError('Unreviewed public files: ' + ', '.join(sorted(extra)))
    return allowed


if __name__ == '__main__':
    print(f'Public boundary verified: {len(audit())} reviewed files')
