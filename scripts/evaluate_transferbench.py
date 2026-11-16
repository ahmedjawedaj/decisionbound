#!/usr/bin/env python3
"""Run a reproducible policy-oracle comparison on author-written JSONL labels.

Labels are not produced by running the policy and are not independently reviewed.

The output records only aggregate labels and source hashes, never raw decision
statements, repository source or oracle justifications. Strictly no live LLMs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from decisionbound.transferbench import load_cases, evaluate  # noqa: E402


def main() -> None:
    """Evaluate held-out labels and optionally write a non-overwritten JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--split', choices=('train','test'), default='test')
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cases = load_cases(args.cases)
    report = evaluate(cases, split=args.split, seed=args.seed,
                      bootstrap_samples=args.bootstrap_samples)
    report['case_file_sha256'] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    report['policy_source_sha256'] = hashlib.sha256((ROOT/'src/decisionbound/transfer.py').read_bytes()).hexdigest()
    serialized = (json.dumps(report, sort_keys=True, indent=2)+'\n').encode()
    if args.output:
        target = args.output.absolute()
        if target.exists() or target.is_symlink() or not target.parent.is_dir():
            parser.error('Output already exists or destination directory is invalid')
        with target.open('xb') as destination:
            destination.write(serialized)
    else:
        sys.stdout.buffer.write(serialized)


if __name__ == '__main__':
    main()
