#!/usr/bin/env python3
"""Check a human annotation file for the CarryBench-Real v2 holdout.

The file is JSON Lines. Line one is a header about the annotator. Every other line is one case.
The check covers the format only. It cannot tell whether a label is right.

    python3 -I check_human_labels.py labels_H1.jsonl --cases cases_v2_holdout_candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LABELS = ("apply", "translate", "abstain", "escalate")
SPECIAL = ("invalid", "no_signal")
STRENGTHS = ("strong", "medium", "weak")
TIERS = ("T1", "T2", "T3", "T4")
MAX_RATIONALE = 400
MAX_NOTES = 1500
HEADER_KEYS = ("annotator_id", "saw_ai_labels", "background", "guide")


def _case_ids(path):
    ids = []
    for line in open(path, encoding="utf-8"):
        row = json.loads(line)
        if "case_id" in row:
            ids.append(row["case_id"])
    return ids


def check(rows: list[dict], expected_ids: list[str]) -> list[str]:
    """Return a list of problems. An empty list means the file is well formed."""
    problems: list[str] = []
    if not rows:
        return ["file is empty"]
    header, cases = rows[0], rows[1:]
    if "case_id" in header:
        problems.append("line 1 must be the header, not a case")
        header, cases = {}, rows
    for key in HEADER_KEYS:
        if key not in header:
            problems.append(f"header is missing '{key}'")
    if header:
        if not isinstance(header.get("annotator_id"), str) or not header.get("annotator_id", "").strip():
            problems.append("header 'annotator_id' must be a non-empty string")
        if not isinstance(header.get("saw_ai_labels"), bool):
            problems.append("header 'saw_ai_labels' must be true or false")
        if not isinstance(header.get("background"), str) or not header.get("background", "").strip():
            problems.append("header 'background' must say, in a few words, why you are qualified")
        if header.get("guide") != "v1.1":
            problems.append("header 'guide' must be exactly 'v1.1'")
    seen: set[str] = set()
    for row in cases:
        cid = row.get("case_id")
        where = f"case {cid}"
        if not isinstance(cid, str):
            problems.append("a record has no case_id")
            continue
        if cid in seen:
            problems.append(f"{where}: appears more than once")
        seen.add(cid)
        label = row.get("label")
        if label not in LABELS + SPECIAL:
            problems.append(f"{where}: label must be one of {', '.join(LABELS + SPECIAL)}")
        allowed = row.get("allowed")
        if allowed is not None and (allowed not in LABELS or allowed == label):
            problems.append(f"{where}: allowed must be null or a different one of the four labels")
        conf = row.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            problems.append(f"{where}: confidence must be a number from 0 to 1")
        if row.get("strength") not in STRENGTHS:
            problems.append(f"{where}: strength must be strong, medium or weak")
        tier = row.get("deciding_tier")
        if label in LABELS and tier not in TIERS:
            problems.append(f"{where}: deciding_tier must be T1 to T4 for a labelled case")
        if label in SPECIAL and tier is not None:
            problems.append(f"{where}: deciding_tier must be null for {label}")
        rationale = row.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            problems.append(f"{where}: rationale is required")
        elif len(rationale) > MAX_RATIONALE:
            problems.append(f"{where}: rationale is longer than {MAX_RATIONALE} characters")
        elif "```" in rationale or "\n" in rationale:
            problems.append(f"{where}: rationale must be one plain sentence with no code block")
        if label == "translate":
            rule = row.get("translated_rule")
            if not isinstance(rule, str) or not rule.strip():
                problems.append(f"{where}: translate needs a translated_rule")
        elif row.get("translated_rule") is not None:
            problems.append(f"{where}: translated_rule must be null unless the label is translate")
        reason = row.get("invalid_reason")
        if label == "invalid":
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"{where}: invalid needs an invalid_reason")
        elif reason is not None:
            problems.append(f"{where}: invalid_reason must be null unless the label is invalid")
        beyond = row.get("read_beyond_pointers")
        if not isinstance(beyond, bool):
            problems.append(f"{where}: read_beyond_pointers must be true or false")
        notes = row.get("notes")
        if notes is not None and (not isinstance(notes, str) or len(notes) > MAX_NOTES):
            problems.append(f"{where}: notes must be text of at most {MAX_NOTES} characters, or null")
        if beyond is True and not (isinstance(notes, str) and notes.strip()):
            problems.append(f"{where}: read_beyond_pointers is true, so notes must say what you read and why")
    missing = sorted(set(expected_ids) - seen)
    extra = sorted(seen - set(expected_ids))
    if missing:
        problems.append("missing cases: " + ", ".join(missing))
    if extra:
        problems.append("unknown cases: " + ", ".join(extra))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("labels")
    ap.add_argument("--cases", required=True, help="the unlabelled candidate file")
    args = ap.parse_args(argv)
    try:
        rows = [json.loads(line) for line in open(args.labels, encoding="utf-8") if line.strip()]
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.labels}: {exc}", file=sys.stderr)
        return 2
    problems = check(rows, _case_ids(args.cases))
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        print(f"{len(problems)} problem(s). Fix them and run the check again.")
        return 1
    print(f"OK: {len(rows) - 1} cases, format is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
