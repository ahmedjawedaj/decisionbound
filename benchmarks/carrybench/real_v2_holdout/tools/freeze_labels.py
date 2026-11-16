#!/usr/bin/env python3
"""Freeze the v2 holdout labels from two blind annotator files.

A case is scored only when both annotators give the same primary label and that
label is one of apply, translate, abstain, escalate. Every other case goes to the
excluded file with both raw labels shown. No adjudication is performed here.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = ("apply", "translate", "abstain", "escalate")


def load(path):
    return {r["case_id"]: r for r in map(json.loads, open(path, encoding="utf-8")) if "case_id" in r}


def main() -> int:
    a = load(ROOT / "annotations" / "labels_A_v1_1.jsonl")
    b = load(ROOT / "annotations" / "labels_B_v1_1.jsonl")
    lines = [json.loads(l) for l in open(ROOT / "cases_v2_holdout_candidates.jsonl", encoding="utf-8")]
    header, cases = lines[0], lines[1:]
    if {c["case_id"] for c in cases} != set(a) or set(a) != set(b):
        raise SystemExit("annotator files do not cover exactly the candidate cases")
    scored, excluded = [], []
    for c in cases:
        i = c["case_id"]
        la, lb = a[i]["label"], b[i]["label"]
        rec = dict(c)
        prov = {"annotators": ["A", "B"], "guide": "v1.1", "set": "v2_holdout",
                "labels": {"A_v1_1_blind": la, "B_v1_1_blind": lb}}
        if la == lb and la in LABELS:
            rec.update(label=la, label_status="agreed_two_blind_llm_annotators", label_provenance=prov)
            scored.append(rec)
        else:
            rec.update(label=None, label_status="contested_excluded", label_provenance=prov)
            excluded.append(rec)
    head = dict(header)
    head.update(dataset_id="carrybench_real_v2_holdout", case_count=len(scored),
                note=("Labels are agreed by two blind LLM annotators of one model family. No human has labelled "
                      "any case. Labels were frozen before any policy or baseline was run on this set. Records "
                      "hold pointers, hashes and paraphrases only, no third party text."))
    with open(ROOT / "cases_v2_holdout_v1.jsonl", "w", encoding="utf-8") as f:
        for r in [head] + scored:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    with open(ROOT / "excluded_v2_holdout.jsonl", "w", encoding="utf-8") as f:
        for r in excluded:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps({"scored": len(scored), "excluded": len(excluded)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
