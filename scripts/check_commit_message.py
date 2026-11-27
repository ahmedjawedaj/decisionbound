#!/usr/bin/env python3
"""Reject assistant authorship markers in one commit message file.

This is the body of the ``commit-msg`` hook. It applies the rules of
``scripts/check_attribution.py`` to the message Git is about to record and to
the author and committer Git would use, so a marker is refused before the
commit exists. It does not restate the rules, it calls them.

Git keeps comment lines and a scissors tail in a message that comes from
``-m`` or ``-F``, and drops them only when the editor flow wrote the message.
The hook cannot see which one it got, so it checks the raw text. The one
exception is a file that still holds the help text Git writes for the editor
(``# Please enter the commit message``). Only then are the ``#`` lines and the
verbose diff after the scissors line left out, because that diff is not part
of the commit. A message that fakes that help line to hide a marker is not a
case this local check tries to stop. CI and the pre-push preflight still do.

Usage: check_commit_message.py MESSAGE_FILE
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_attribution  # noqa: E402

EDITOR_HELP = '# Please enter the commit message'
SCISSORS = re.compile(r'^# -+ >8 -+\s*$')
IDENT = re.compile(r'^(?P<name>.*) <(?P<email>[^>]*)> \d+ [+-]\d{4}\s*$')


def clean(text: str) -> str:
    """Return the text to check: raw, unless it is an editor file with Git's help text."""
    if EDITOR_HELP not in text:
        return text
    kept = []
    for line in text.splitlines():
        if SCISSORS.match(line):
            break
        if not line.startswith('#'):
            kept.append(line)
    return '\n'.join(kept)


def identity(which: str) -> tuple[str, str] | None:
    """Return the name and email Git would use, or None when Git cannot say."""
    done = subprocess.run(['git', 'var', f'GIT_{which}_IDENT'], capture_output=True,
                          text=True, encoding='utf-8', errors='replace')
    match = IDENT.match(done.stdout) if done.returncode == 0 else None
    return (match.group('name'), match.group('email')) if match else None


def problems_for(message: str) -> list[str]:
    """Return the attribution problems in a message and the identities Git would record."""
    found = check_attribution.text_problems('The commit message', clean(message))
    for which in ('AUTHOR', 'COMMITTER'):
        who = identity(which)
        if who:
            found += check_attribution.person_problems(f'The {which.lower()}', *who)
    return found


def main(argv: list[str]) -> int:
    """Check the message file named in argv and print the problems found."""
    if len(argv) != 2:
        print('Usage: check_commit_message.py MESSAGE_FILE', file=sys.stderr)
        return 2
    message = Path(argv[1]).read_text(encoding='utf-8', errors='replace')
    found = problems_for(message)
    if found:
        print('Commit refused by the attribution rules:', file=sys.stderr)
        for problem in found:
            print(' -', problem, file=sys.stderr)
        print('Remove the marker and commit again. See the Authorship section of CONTRIBUTING.md.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
