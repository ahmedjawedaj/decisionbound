#!/usr/bin/env python3
"""Validate a release from trusted main before executing its tagged build code."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.request

WORKFLOWS = ('ci.yml', 'quality-gate.yml', 'platform-certification.yml')
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+|b[0-9]+|rc[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?')


def validate_tag(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError('Use a version tag such as v0.13.0a1 or v0.13.0')
    return tag[1:]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def verify_source(root: Path, tag: str) -> tuple[str, str]:
    """Read tagged metadata as data; never import or execute tagged Python."""
    version = validate_tag(tag)
    sha = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', sha, 'origin/main'], check=True)
    metadata = tomllib.loads(git(root, 'show', sha + ':pyproject.toml'))['project']
    tree = ast.parse(git(root, 'show', sha + ':src/decisionbound/__init__.py'))
    runtime = [node.value.value for node in tree.body
               if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
               and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets)]
    citation = [line.split(':', 1)[1].strip().strip('"\'')
                for line in git(root, 'show', sha + ':CITATION.cff').splitlines()
                if line.startswith('version:')]
    if metadata['name'] != 'decisionbound' or metadata['version'] != version or runtime != [version] or citation != [version]:
        raise ValueError('Tag, pyproject.toml, __version__ and CITATION.cff must match')
    return sha, version


def fetch_json(url: str, token: str | None = None) -> dict | None:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not token:
                return None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError('unreachable')


def require_unused_version(version: str, fetch=fetch_json) -> None:
    if fetch(f'https://pypi.org/pypi/decisionbound/{version}/json') is not None:
        raise ValueError('This version is already on PyPI; bump the version and publish a new release')


def wait_for_certification(repo: str, sha: str, token: str, *, wait_seconds: int = 7200,
                           fetch=fetch_json, clock=time.monotonic, sleep=time.sleep) -> None:
    """Wait for exact-commit main CI; refuse any failed latest run or timeout."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    deadline = clock() + wait_seconds
    while True:
        pending = []
        for workflow in WORKFLOWS:
            url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?branch=main&event=push&head_sha={sha}&per_page=100'
            response = fetch(url, token)
            runs = [r for r in response['workflow_runs']
                    if r['head_sha'] == sha and r['head_branch'] == 'main' and r['event'] == 'push']
            if not runs:
                pending.append(workflow)
                continue
            run = max(runs, key=lambda r: (r['run_number'], r['run_attempt']))
            if run['status'] != 'completed':
                pending.append(workflow)
            elif run['conclusion'] != 'success':
                raise ValueError('Main certification failed: ' + workflow)
        if not pending:
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Timed out waiting for main certification: ' + ', '.join(pending))
        print('Waiting for main certification: ' + ', '.join(pending), flush=True)
        sleep(min(60, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sha, version = verify_source(root, args.tag)
    require_unused_version(version)
    wait_for_certification(os.environ['GH_REPO'], sha, os.environ['GH_TOKEN'])
    print(f'Certified release {version} at {sha}')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(f'sha={sha}\nversion={version}\n')


if __name__ == '__main__':
    main()
