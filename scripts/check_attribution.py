#!/usr/bin/env python3
"""Reject AI authorship markers in a pull request.

The maintainer's rule is that every commit is authored by a person and that no
assistant appears as author, co-author or in a generated footer. This check
reads the commits of a pull request and its title and body, and fails when it
finds one of these markers:

* an author or committer whose name is Claude or whose address is at
  anthropic.com
* a ``Co-Authored-By`` trailer that names Claude or Anthropic
* a ``Claude-Session`` trailer
* a generated-by footer or a claude.ai/code session link

Plain mentions of a model in prose, for example a commit about a Claude
adapter, are allowed. Lines that look like a trailer or a footer fail even in a
document that quotes them. The check guards against accidents, not against
someone who is trying to evade it, so it does not fold lookalike letters.
Dependabot pull requests are checked for author and committer only, because
their text quotes upstream release notes.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata

COAUTHOR = re.compile(r'^\s*co-authored-by\s*:.*(claude|anthropic)', re.I | re.M)
SESSION = re.compile(r'^\s*claude-session\s*:', re.I | re.M)
FOOTER = re.compile(r'generated\s+(with|by)\s+\[?\s*claude\s+code|claude\.(ai|com)/code/session', re.I)
INVISIBLE = dict.fromkeys(map(ord, '\u200b\u200c\u200d\u2060\ufeff'))
NAME = re.compile(r'^\s*claude\b', re.I)
EMAIL = re.compile(r'@([\w.-]+\.)?anthropic\.com\s*$', re.I)


def normalize(text: str) -> str:
    """Fold compatibility forms such as a non-breaking space and drop invisible characters."""
    return unicodedata.normalize('NFKC', text).translate(INVISIBLE)


def text_problems(label: str, text: str) -> list[str]:
    text = normalize(text)
    problems = []
    if COAUTHOR.search(text):
        problems.append(f'{label} has a Co-Authored-By trailer naming Claude or Anthropic')
    if SESSION.search(text):
        problems.append(f'{label} has a Claude-Session trailer')
    if FOOTER.search(text):
        problems.append(f'{label} has a generated-by footer or a claude.ai session link')
    return problems


def person_problems(label: str, name: str, email: str) -> list[str]:
    name, email = normalize(name), normalize(email)
    if NAME.search(name) or EMAIL.search(email):
        return [f'{label} is {name} <{email}>, which is an assistant identity']
    return []


def check(commits: list[dict], title: str = '', body: str = '', check_text: bool = True) -> list[str]:
    """Return a list of problems. Each commit is a dict with sha, author_name,
    author_email, committer_name, committer_email and message. With
    check_text false only the author and committer identities are checked."""
    problems = []
    if check_text:
        problems += text_problems('The pull request title', title) + text_problems('The pull request body', body)
    for commit in commits:
        short = commit['sha'][:7]
        problems += person_problems(f'Author of {short}', commit['author_name'], commit['author_email'])
        problems += person_problems(f'Committer of {short}', commit['committer_name'], commit['committer_email'])
        if check_text:
            problems += text_problems(f'Commit {short}', commit['message'])
    return problems


def is_shallow() -> bool:
    out = subprocess.run(['git', 'rev-parse', '--is-shallow-repository'],
                         check=True, capture_output=True, text=True).stdout
    return out.strip() == 'true'


def read_commits(base: str, head: str) -> list[dict]:
    """Read the commits in base..head. A shallow clone would stop the log at its
    boundary and skip commits without any error, so fetch the full history or fail."""
    if is_shallow():
        subprocess.run(['git', 'fetch', '--quiet', '--unshallow'], check=True, capture_output=True, text=True)
        if is_shallow():
            raise subprocess.CalledProcessError(1, 'git fetch --unshallow', stderr='the clone is still shallow')
    fmt = '%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e'
    out = subprocess.run(['git', 'log', f'--format={fmt}', f'{base}..{head}'],
                         check=True, capture_output=True, text=True).stdout
    commits = []
    for record in out.split('\x1e'):
        if not record.strip():
            continue
        sha, an, ae, cn, ce, message = record.lstrip('\n').split('\x1f', 5)
        commits.append({'sha': sha, 'author_name': an, 'author_email': ae,
                        'committer_name': cn, 'committer_email': ce, 'message': message})
    return commits


def main() -> int:
    base, head = os.environ.get('BASE_SHA'), os.environ.get('HEAD_SHA')
    if not base or not head:
        print('BASE_SHA and HEAD_SHA are required', file=sys.stderr)
        return 2
    try:
        commits = read_commits(base, head)
    except subprocess.CalledProcessError as exc:
        print('Could not read the commit range:', (exc.stderr or '').strip(), file=sys.stderr)
        return 2
    bot = os.environ.get('PR_AUTHOR', '') == 'dependabot[bot]'
    problems = check(commits, os.environ.get('PR_TITLE', ''), os.environ.get('PR_BODY', ''), check_text=not bot)
    if problems:
        print('Attribution check failed:')
        for problem in problems:
            print(' -', problem)
        return 1
    print(f'Attribution check passed for {len(commits)} commit(s)' + ('' if bot else ', the title and the body'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
