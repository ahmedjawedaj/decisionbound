#!/usr/bin/env python3
"""Build a blind annotation packet for human annotators.

The packet holds only what a blind annotator may see: the protocol, the v1.1 guide, the
unlabelled candidate cases, the pins, the two tools needed to fetch and verify pointers, the
label checker and a blank label template. It leaves out every AI label, note and result.

    python3 make_blind_packet.py --out /path/to/new_empty_dir

Give the annotator the folder or a zip of it, not the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOLDOUT = HERE.parent.parent
V1 = HOLDOUT.parent / "real_v1"

# (source, name inside the packet)
FILES = [
    (HERE.parent / "PROTOCOL.md", "PROTOCOL.md"),
    (V1 / "guides" / "guide_v1_1.md", "GUIDE_v1_1.md"),
    (HOLDOUT / "cases_v2_holdout_candidates.jsonl", "cases_v2_holdout_candidates.jsonl"),
    (HOLDOUT / "pins.csv", "pins.csv"),
    (V1 / "tools" / "fetch_pins.py", "tools/fetch_pins.py"),
    (V1 / "tools" / "mine_decisions.py", "tools/mine_decisions.py"),
    (HERE / "check_human_labels.py", "tools/check_human_labels.py"),
]
FORBIDDEN_KEYS = ("label", "gold", "verifier_note", "label_provenance")


def template_rows(candidates: Path) -> list[dict]:
    header = {"annotator_id": "", "saw_ai_labels": None, "background": "", "guide": "v1.1"}
    rows = [header]
    for line in open(candidates, encoding="utf-8"):
        row = json.loads(line)
        if "case_id" not in row:
            continue
        rows.append({
            "case_id": row["case_id"], "label": None, "allowed": None, "confidence": None,
            "strength": None, "deciding_tier": None, "rationale": "", "translated_rule": None,
            "invalid_reason": None, "read_beyond_pointers": None, "notes": None,
        })
    return rows


def assert_unlabelled(candidates: Path) -> None:
    for line in open(candidates, encoding="utf-8"):
        row = json.loads(line)
        if "case_id" not in row:
            continue
        bad = [k for k in FORBIDDEN_KEYS if k in row]
        if bad or row.get("label_status") != "unlabelled_candidate":
            raise SystemExit(f"refusing to build: {row.get('case_id')} is not an unlabelled candidate ({bad})")


def build(out: Path) -> list[str]:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty")
    assert_unlabelled(HOLDOUT / "cases_v2_holdout_candidates.jsonl")
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for src, name in FILES:
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        names.append(name)
    with open(out / "labels_template.jsonl", "w", encoding="utf-8") as f:
        for row in template_rows(HOLDOUT / "cases_v2_holdout_candidates.jsonl"):
            f.write(json.dumps(row, sort_keys=True) + "\n")
    names.append("labels_template.jsonl")
    lines = []
    for name in sorted(names):
        digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    (out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(names) + ["MANIFEST.sha256"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    for name in build(Path(args.out)):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
