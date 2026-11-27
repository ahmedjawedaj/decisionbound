#!/usr/bin/env python3
"""Turn the optional git hooks in ``.githooks`` on or off for this clone.

Nothing sets ``core.hooksPath`` except this script, and only when you run it.
It changes the local repository config only, never the global one. It will not
replace a different ``core.hooksPath`` you already have unless you pass
``--force``, and ``--uninstall`` removes the setting only when it points at
``.githooks``.

Usage:
  python scripts/install_hooks.py              set core.hooksPath to .githooks
  python scripts/install_hooks.py --uninstall  unset it
  python scripts/install_hooks.py --repo PATH  act on another checkout
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = '.githooks'
KEY = 'core.hooksPath'


def git_config(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``git config --local`` in the repository."""
    return subprocess.run(['git', '-C', str(repo), 'config', '--local', *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def current(repo: Path) -> str | None:
    """Return the local core.hooksPath, or None when it is not set."""
    done = git_config(repo, '--get', KEY)
    return done.stdout.strip() if done.returncode == 0 else None


def install(repo: Path, force: bool) -> int:
    """Set core.hooksPath to .githooks unless another value is in the way."""
    if not (repo / HOOKS).is_dir():
        print(f'There is no {HOOKS} directory in {repo}.', file=sys.stderr)
        return 2
    existing = current(repo)
    if existing == HOOKS:
        print(f'{KEY} is already {HOOKS}. Nothing changed.')
        return 0
    if existing and not force:
        print(f'{KEY} is already set to {existing!r}. Nothing changed. Use --force to replace it.',
              file=sys.stderr)
        return 1
    done = git_config(repo, KEY, HOOKS)
    if done.returncode != 0:
        print(done.stderr.strip() or 'git config failed', file=sys.stderr)
        return 2
    if existing:
        print(f'Replaced {KEY} {existing!r} with {HOOKS!r} in the local config.')
    else:
        print(f'Set {KEY} to {HOOKS!r} in the local config.')
    print('Active hooks: commit-msg (attribution rules) and pre-push (scripts/preflight.py).')
    print('Undo with: python scripts/install_hooks.py --uninstall')
    return 0


def uninstall(repo: Path) -> int:
    """Unset core.hooksPath when it points at .githooks."""
    existing = current(repo)
    if existing is None:
        print(f'{KEY} is not set. Nothing changed.')
        return 0
    if existing != HOOKS:
        print(f'{KEY} is {existing!r}, not {HOOKS!r}, so it was left alone.', file=sys.stderr)
        return 1
    done = git_config(repo, '--unset', KEY)
    if done.returncode != 0:
        print(done.stderr.strip() or 'git config failed', file=sys.stderr)
        return 2
    print(f'Unset {KEY} in the local config. The hooks are off.')
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the options and install or uninstall."""
    parser = argparse.ArgumentParser(description='Turn the optional git hooks on or off.')
    parser.add_argument('--uninstall', action='store_true', help='unset core.hooksPath')
    parser.add_argument('--force', action='store_true', help='replace a different core.hooksPath')
    parser.add_argument('--repo', type=Path, default=ROOT, help='checkout to act on (default: this one)')
    args = parser.parse_args(argv)
    if args.uninstall:
        return uninstall(args.repo)
    return install(args.repo, args.force)


if __name__ == '__main__':
    raise SystemExit(main())
