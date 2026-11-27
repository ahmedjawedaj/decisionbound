#!/usr/bin/env python3
"""Turn the optional git hooks in ``.githooks`` on or off for this clone.

Nothing sets ``core.hooksPath`` except this script, and only when you run it.
It changes the local repository config only, never the global one. It will not
replace a different ``core.hooksPath`` you already have unless you pass
``--force``. Setting ``core.hooksPath`` also switches off every hook in
``.git/hooks``, so the installer refuses while that folder holds a hook of
yours or while a global or system ``core.hooksPath`` exists, again unless you
pass ``--force``. ``--force`` records the value it replaces, and
``--uninstall`` puts it back and removes the setting only when it points at
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
SAVED = 'decisionbound.previoushookspath'


def git_config(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``git config --local`` in the repository."""
    return subprocess.run(['git', '-C', str(repo), 'config', '--local', *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def current(repo: Path) -> str | None:
    """Return the local core.hooksPath, or None when it is not set."""
    done = git_config(repo, '--get', KEY)
    return done.stdout.strip() if done.returncode == 0 else None


def toplevel(repo: Path) -> Path | None:
    """Return the root of the repository that contains ``repo``, or None."""
    done = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else None


def shadowed(repo: Path) -> list[str]:
    """List the hooks that setting core.hooksPath would switch off."""
    found = []
    done = subprocess.run(['git', '-C', str(repo), 'config', '--show-scope', '--get-all', KEY],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    for line in done.stdout.splitlines():
        scope, _, value = line.partition('\t')
        if scope in ('global', 'system'):
            found.append(f'a {scope} {KEY} of {value!r}')
    where = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--git-path', 'hooks'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    folder = (repo / where.stdout.strip()) if where.returncode == 0 and where.stdout.strip() else None
    if folder and folder.is_dir():
        found += [f'.git/hooks/{item.name}' for item in sorted(folder.iterdir())
                  if item.is_file() and not item.name.endswith('.sample')]
    return found


def install(repo: Path, force: bool) -> int:
    """Set core.hooksPath to .githooks unless another value is in the way."""
    if not (repo / HOOKS).is_dir():
        print(f'There is no {HOOKS} directory in {repo}.', file=sys.stderr)
        return 2
    top = toplevel(repo)
    if top is None or top != repo.resolve():
        print(f'{repo} is not the root of a git checkout, so nothing was changed.', file=sys.stderr)
        return 2
    existing = current(repo)
    if existing == HOOKS:
        print(f'{KEY} is already {HOOKS}. Nothing changed.')
        return 0
    if existing and not force:
        print(f'{KEY} is already set to {existing!r}. Nothing changed. Use --force to replace it.',
              file=sys.stderr)
        return 1
    others = shadowed(repo)
    if others and not force:
        print(f'Setting {KEY} would switch off hooks you already have:', file=sys.stderr)
        for item in others:
            print(f' - {item}', file=sys.stderr)
        print('Nothing changed. Use --force if you accept that, or run the preflight by hand '
              '(python scripts/preflight.py).', file=sys.stderr)
        return 1
    if existing:
        git_config(repo, SAVED, existing)
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
    saved = git_config(repo, '--get', SAVED)
    previous = saved.stdout.strip() if saved.returncode == 0 else ''
    done = git_config(repo, '--unset', KEY)
    if done.returncode != 0:
        print(done.stderr.strip() or 'git config failed', file=sys.stderr)
        return 2
    git_config(repo, '--unset', SAVED)
    if previous:
        git_config(repo, KEY, previous)
        print(f'Restored {KEY} to {previous!r}. The DecisionBound hooks are off.')
    else:
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
