#!/usr/bin/env python3
"""Assemble the unlabelled holdout files from a shortlist and a curation file.

Pointer-only output: repository, pinned commit, path, line range, SPDX, hand
written paraphrase and a SHA-256 of the whitespace-normalised span. The case
file has no `label` field, so the v1 scorers (which keep only labelled cases)
cannot score it until labels are added blind. The selection log with strata is
written to a separate file that annotators must not read.

  python3 -I tools/assemble_cases.py --shortlist shortlist.jsonl --curation curation_v2_holdout.json \
      --pool-pins pool_pins.csv --mined mined.jsonl --clones-dir ./clones --seed 20261117 --outdir .
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mine_decisions", HERE.parents[1] / "real_v1" / "tools" / "mine_decisions.py")
md = importlib.util.module_from_spec(_spec)
sys.modules["mine_decisions"] = md
_spec.loader.exec_module(md)

FIELDS = ["repo", "url", "sha", "spdx", "evidence_files", "language", "notes", "license_files", "n_py", "n_ts"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for a in ("--shortlist", "--curation", "--pool-pins", "--mined", "--clones-dir", "--outdir"):
        ap.add_argument(a, required=True)
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args()
    out = Path(args.outdir)
    pool = {r["repo"]: r for r in csv.DictReader(open(args.pool_pins, encoding="utf-8"))}
    short = {c["shortlist_id"]: c for c in map(json.loads, open(args.shortlist, encoding="utf-8"))}
    cur = json.load(open(args.curation, encoding="utf-8"))
    ids = sorted(cur)
    random.Random(args.seed).shuffle(ids)  # case order carries no stratum information
    cases, log, used = [], [], set()
    clones = Path(args.clones_dir)
    for n, sid in enumerate(ids, 1):
        s, k = short[sid], cur[sid]
        sp, tp = pool[s["source"]["repo"]], pool[s["target_repo"]]
        used |= {sp["repo"], tp["repo"]}
        evidence = []
        for e in s["target_evidence"]:
            para = k["evidence"].get(e["kind"])
            if para is None:
                continue
            evidence.append({"kind": e["kind"], "path": e["path"], "line_start": e["line_start"], "line_end": e["line_end"],
                             "text_sha256": e["text_sha256"], "paraphrase": para})
        if not s["target_evidence"] or not evidence:  # target holds no text on the topic: point at its guidance file instead
            tree = [p for p, _ in md.list_tree(md.clone_path(clones, tp["repo"]), tp["sha"])]
            guide = sorted((p for p in tree if md.signal_class(p) == "agent_instructions"), key=lambda p: (p.count("/"), p)) \
                or sorted((p for p in tree if md.signal_class(p) == "contributing"), key=lambda p: (p.count("/"), p))
            if guide:
                evidence.append({"kind": "path_present", "path": guide[0], "line_start": 0, "line_end": 0, "text_sha256": None,
                                 "paraphrase": "contributor or agent guidance file exists at this path"})
        src = s["source"]
        cases.append({
            "case_id": "cbh-%03d" % n, "family": s["family"], "format": "carrybench-real-case-v1",
            "label_status": "unlabelled_candidate",
            "source": {"repo": sp["repo"], "url": sp["url"], "sha": sp["sha"], "spdx": sp["spdx"], "path": src["path"],
                       "line_start": src["line_start"], "line_end": src["line_end"], "text_sha256": src["text_sha256"],
                       "paraphrase": k["source_paraphrase"], "scope_conditions": k["scope_conditions"]},
            "target": {"repo": tp["repo"], "url": tp["url"], "sha": tp["sha"], "spdx": tp["spdx"], "task": k["task"],
                       "evidence": evidence}})
        log.append({"case_id": "cbh-%03d" % n, "shortlist_id": sid, "stratum": s["stratum"],
                    "cross_ecosystem": s["cross_ecosystem"], "family": s["family"]})
    cases.sort(key=lambda c: c["case_id"])
    header = {"case_count": len(cases), "dataset_id": "carrybench_real_v2_holdout", "format": "carrybench-real-file-header-v1",
              "note": "UNLABELLED candidates. No label, no gold, no verifier note. To be labelled blind before any policy is fixed. Records hold pointers, hashes and paraphrases only, no third party text.",
              "synthetic": False, "validated": False}
    with open(out / "cases_v2_holdout_candidates.jsonl", "w", encoding="utf-8") as fh:
        for r in [header] + cases:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    with open(out / "selection_log_DO_NOT_SHOW_ANNOTATORS.jsonl", "w", encoding="utf-8") as fh:
        for r in sorted(log, key=lambda r: r["case_id"]):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    with open(out / "pins.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for repo in sorted(used):
            w.writerow({f: pool[repo][f] for f in FIELDS})
    n = 0
    with open(out / "config_facts_v2_holdout.jsonl", "w", encoding="utf-8") as fh:
        for line in open(args.mined, encoding="utf-8"):
            r = json.loads(line)
            if r["kind"] == "config_fact" and r["repo"] in used:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
                n += 1
    print(json.dumps({"cases": len(cases), "repos": len(used), "config_facts": n}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
