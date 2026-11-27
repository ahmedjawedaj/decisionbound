#!/usr/bin/env python3
"""Run the checks that kept failing in CI before you push.

The steps run in order and the run stops at the first failure. The failure
message names the step and says what to run to fix it. The steps are:

* publication: ``scripts/check_publication.py``
* docs: ``scripts/check_docs.py``
* api-reference: regenerate ``site_docs/api-reference.md`` with
  ``scripts/generate_api_docs.py`` and fail when it differs from HEAD. It
  refuses to run while ``src/`` or the generator has uncommitted changes,
  because the result would not describe what is committed
* attribution: ``scripts/check_attribution.py`` over the commits that are not
  yet on ``origin/main``

The preflight is not a copy of CI. It does not run pytest, ``build_site.py``,
``verify_docs.py`` or the pull request title and body check.

Everything is read-only except the api-reference step, which rewrites
``site_docs/api-reference.md``. When that file drifts, the regenerated file is
left in place and the message says so. Nothing is restored behind your back.

Options:
  --fast       skip the slow steps (the history walk in the attribution step)
  --only NAME  run only the named step, can be repeated
  --base REV   the revision the pending commits start after (default: the
               merge base with origin/main, then main)
  --head REV   the commit to check instead of HEAD (the pre-push hook uses it)
  --list       print the step names and exit

Exit status is 0 when every selected step passes, 1 when a step fails and 2
when the preflight itself cannot run.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
API_REFERENCE = 'site_docs/api-reference.md'


@dataclass
class Outcome:
    """Result of one step. ``fix`` says what the contributor should do next."""

    ok: bool
    output: str = ''
    fix: str = ''


@dataclass
class Step:
    """A named check. ``slow`` steps are skipped by ``--fast``."""

    name: str
    run: Callable[[argparse.Namespace], Outcome]
    slow: bool = False


def run_process(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a command in the repository root and return its status and combined output."""
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    return done.returncode, (done.stdout or '') + (done.stderr or '')


def script_step(script: str, fix: str) -> Callable[[argparse.Namespace], Outcome]:
    """Build a step that runs one script from ``scripts/`` and passes when it exits 0."""
    def run(_args: argparse.Namespace) -> Outcome:
        code, output = run_process([sys.executable, f'scripts/{script}'])
        return Outcome(code == 0, output, fix)
    return run


def api_reference(_args: argparse.Namespace) -> Outcome:
    """Regenerate the API reference in place and compare it with HEAD."""
    fix = (f'Run python scripts/generate_api_docs.py, then commit {API_REFERENCE}. '
           'If it refuses, add the missing docstrings it lists.')
    code, dirty = run_process(['git', 'status', '--porcelain', '--', 'src', 'scripts/generate_api_docs.py'])
    if code == 0 and dirty.strip():
        return Outcome(False, 'These files have uncommitted changes, so the regenerated reference would '
                       f'not describe what is committed:\n{dirty}',
                       'Commit or stash the changes in src/ and scripts/generate_api_docs.py, then run '
                       'the preflight again.')
    code, output = run_process([sys.executable, 'scripts/generate_api_docs.py'])
    if code != 0:
        return Outcome(False, output, fix)
    code, _ = run_process(['git', 'ls-files', '--error-unmatch', API_REFERENCE])
    if code != 0:
        return Outcome(False, f'{API_REFERENCE} is not tracked by git.\n', fix)
    code, output = run_process(['git', 'diff', '--quiet', 'HEAD', '--', API_REFERENCE])
    if code == 0:
        return Outcome(True)
    if code == 1:
        return Outcome(False,
                       f'The regenerated {API_REFERENCE} differs from HEAD. '
                       'The regenerated file is left in place. '
                       f'Review it with: git diff HEAD -- {API_REFERENCE}\n',
                       f'Commit the regenerated {API_REFERENCE}.')
    return Outcome(False, output, 'Run the preflight inside a git checkout that has at least one commit.')


def find_base(override: str | None, head: str = 'HEAD') -> str | None:
    """Return the revision after which commits count as pending, or None."""
    if override:
        return override
    for ref in ('origin/main', 'main'):
        code, output = run_process(['git', 'merge-base', head, ref])
        if code == 0 and output.strip():
            return output.strip()
    return None


def attribution(args: argparse.Namespace) -> Outcome:
    """Run the real attribution check over base..HEAD through its own CLI."""
    code, head = run_process(['git', 'rev-parse', '--verify', f'{args.head or "HEAD"}^{{commit}}'])
    if code != 0:
        return Outcome(False, head, 'Run the preflight inside a git checkout that has at least one commit, '
                       'and give --head a commit that exists.')
    base = find_base(args.base, head.strip())
    if base is None:
        return Outcome(False, 'Could not find origin/main or main to start the commit range.\n',
                       'Fetch it with: git fetch origin main. Or pass the revision your commits start '
                       'after, for example: python scripts/preflight.py --base origin/main')
    env = {k: v for k, v in os.environ.items() if k not in ('PR_TITLE', 'PR_BODY', 'PR_AUTHOR')}
    env['BASE_SHA'], env['HEAD_SHA'] = base, head.strip()
    code, output = run_process([sys.executable, 'scripts/check_attribution.py'], env)
    if code == 2:
        return Outcome(False, output,
                       'The check could not read the commit history. Make sure the base revision exists '
                       'and that a shallow clone can fetch its history (git fetch --unshallow), then run '
                       'python scripts/preflight.py again.')
    return Outcome(code == 0, output,
                   'Rewrite the listed commits so that a person is the author and no Claude or '
                   'Anthropic trailer or footer remains, for example with git commit --amend or '
                   'git rebase. See the Authorship section of CONTRIBUTING.md.')


STEPS = [
    Step('publication', script_step(
        'check_publication.py',
        'Add each new tracked file to PUBLIC_CONTENT.json, keep its files list sorted, '
        'then run python scripts/check_publication.py.')),
    Step('docs', script_step(
        'check_docs.py',
        'Fix the broken local links it lists, then run python scripts/check_docs.py.')),
    Step('api-reference', api_reference),
    Step('attribution', attribution, slow=True),
]


def main(argv: list[str] | None = None) -> int:
    """Run the selected steps in order and stop at the first failure."""
    parser = argparse.ArgumentParser(description='Run the local checks before you push.')
    parser.add_argument('--fast', action='store_true', help='skip the slow steps')
    parser.add_argument('--only', action='append', metavar='NAME', help='run only this step, can be repeated')
    parser.add_argument('--base', help='revision the pending commits start after')
    parser.add_argument('--head', help='commit to check instead of HEAD')
    parser.add_argument('--list', action='store_true', help='print the step names and exit')
    args = parser.parse_args(argv)
    names = [step.name for step in STEPS]
    if args.list:
        print('\n'.join(names))
        return 0
    unknown = [name for name in args.only or [] if name not in names]
    if unknown:
        print(f'Unknown step: {", ".join(unknown)}. Steps are: {", ".join(names)}', file=sys.stderr)
        return 2
    selected = [step for step in STEPS if not args.only or step.name in args.only]
    for number, step in enumerate(selected, 1):
        label = f'[{number}/{len(selected)}] {step.name}'
        if args.fast and step.slow:
            print(f'{label} ... skipped (--fast)')
            continue
        print(f'{label} ...', flush=True)
        outcome = step.run(args)
        if not outcome.ok:
            print(outcome.output.rstrip())
            print(f'\nPreflight failed at step "{step.name}".')
            print(f'To fix: {outcome.fix}')
            return 1
        print(f'{label} ok')
    print('Preflight passed.' + (' The slow steps were skipped.' if args.fast else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
