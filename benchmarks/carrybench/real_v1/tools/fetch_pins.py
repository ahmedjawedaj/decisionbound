#!/usr/bin/env python3
"""Fetch the pinned public repositories that CarryBench-Real v1 points into.

Standard library plus the git CLI. Network access is needed once. Each clone
is blobless and shallow, checked out at the pinned commit only, so nothing from
a repository is executed or installed. Extra commits named by history pointers
(revert evidence) are fetched by hash.

  python3 -I tools/fetch_pins.py --pins pins.csv --cases cases_v1.jsonl \
      --cases excluded_v1.jsonl --clones-dir ./clones

Clones land in <clones-dir>/<owner>__<repo>, the layout the other tools read.
Use --all to fetch every pinned repository instead of only the ones the case
file uses.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


def git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def used_repos(files: list[Path]) -> tuple[set[str], dict[str, set[str]]]:
    repos: set[str] = set()
    commits: dict[str, set[str]] = {}
    for cases in files:
        for line in cases.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if "case_id" not in c:
                continue
            repos |= {c["source"]["repo"], c["target"]["repo"]}
            for e in c["target"].get("evidence", []):
                if e.get("commit"):
                    commits.setdefault(c["target"]["repo"], set()).add(e["commit"])
    return repos, commits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pins", required=True)
    ap.add_argument("--cases", action="append", default=[], help="case file, repeatable (scored and excluded)")
    ap.add_argument("--clones-dir", required=True)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    pins = {r["repo"]: r for r in csv.DictReader(open(args.pins, encoding="utf-8"))}
    commits: dict[str, set[str]] = {}
    if args.all or not args.cases:
        wanted = set(pins)
    else:
        wanted, commits = used_repos([Path(f) for f in args.cases])
    clones = Path(args.clones_dir)
    clones.mkdir(parents=True, exist_ok=True)
    failed = 0
    for repo in sorted(wanted):
        pin = pins.get(repo)
        if pin is None:
            print(f"no pin for {repo}", file=sys.stderr)
            failed += 1
            continue
        dest = clones / repo.replace("/", "__")
        try:
            if not (dest / ".git").exists():
                git("clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", pin["url"], str(dest))
            git("fetch", "-q", "--depth", "1", "origin", pin["sha"], cwd=dest)
            git("checkout", "-q", "--detach", pin["sha"], cwd=dest)
            for sha in sorted(commits.get(repo, ())):
                git("fetch", "-q", "--depth", "1", "origin", sha, cwd=dest)
            print(f"ok {repo} {pin['sha'][:12]}")
        except subprocess.CalledProcessError as exc:
            failed += 1
            print(f"FAIL {repo}: {(exc.stderr or '').strip()[:200]}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
