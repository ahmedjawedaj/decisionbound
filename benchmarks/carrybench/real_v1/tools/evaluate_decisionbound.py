#!/usr/bin/env python3
"""Score DecisionBound's shipped transfer methods on CarryBench-Real v1.

Needs the decisionbound package (pip install it, or PYTHONPATH=src from a repo
checkout) and the pinned clones.

The shipped methods read a Decision and a Project, not repository text, so a
label-blind adapter builds those objects from each case. The adapter is part of
this evaluation, not part of DecisionBound, and its choices change the result.
Both settings are reported so the reader can see how much.

Adapter (fixed before any score was computed, uses no label):
  target technologies   lang:<language from pins.csv> plus <family>:<member> for
                        every mined config fact of the target repository
  required tags         <family>:<member> for each tool the source text
                        prescribes (practice families have no tool, so none)
  blocked tags          none
  status                approved
  owner                 the same owner string on both sides

Settings
  faithful   scope project. A decision recorded in one repository is a
             project decision, which the shipped policy never carries over.
  personal   scope personal with the same owner. The most generous reading
             the shipped policy allows.
  personal+blocklist
             as personal, and the decision id is in forbidden_ids when the
             case's family is an authority family. This reads the family
             field, so it is not label-free in practice. It shows what an
             owner-maintained blocklist would buy.

Methods: no-transfer, full-transfer, tags-only and decisionbound, exactly as
src/decisionbound/transferbench.py defines them.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate_baselines as EB  # noqa: E402

from decisionbound.models import Decision, Evidence, Project  # noqa: E402
from decisionbound.transferbench import METHODS, TransferCase, predict  # noqa: E402

OWNER = "owner"


def tech_tags(repo: str, language: str, stance: dict) -> tuple[str, ...]:
    tags = {f"lang:{part.strip().lower()}" for part in language.replace("+", " ").split() if part.strip()}
    tags |= {f"{fam}:{mem}" for fam, mem in stance.get(repo, set())}
    return tuple(sorted(tags))


def build_case(feat: "EB.Features", pins: dict, stance: dict, scope: str, blocklist: bool) -> TransferCase:
    c = feat.case
    src_repo, tgt_repo = c["source"]["repo"], c["target"]["repo"]
    required = tuple(sorted({f"{fam}:{mem}" for fam, mem, pol in feat.src_mentions
                             if pol == "prescribe" and not fam.startswith("practice_")}))
    decision = Decision(
        decision_id=c["case_id"], source_project_id=src_repo, statement=c["source"]["paraphrase"],
        rationale="adapter", evidence=Evidence(c["source"]["path"] or "unknown", c["source"].get("text_sha256") or "0" * 64),
        scope=scope, status="approved", required_tags=required, blocked_tags=(), owner=OWNER)
    target = Project(project_id=tgt_repo, name=tgt_repo,
                     technologies=tech_tags(tgt_repo, pins[tgt_repo]["language"], stance), owner=OWNER)
    forbidden = (c["case_id"],) if blocklist and c["family"] in EB.AUTHORITY_FAMILIES else ()
    return TransferCase(c["case_id"], "test", tgt_repo, target, decision, forbidden,
                        frozenset({c["label"]}), False, "carrybench real label")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--clones-dir", required=True)
    ap.add_argument("--mined", required=True, help="config_facts_v1.jsonl")
    ap.add_argument("--pins", required=True)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    cases = EB.load_cases(Path(args.cases))
    stance = EB.load_stance(Path(args.mined))
    pins = {r["repo"]: r for r in csv.DictReader(open(args.pins, encoding="utf-8"))}
    feats = [EB.Features(c, Path(args.clones_dir), stance) for c in cases]
    gold = [c["label"] for c in cases]

    settings = {"faithful": ("project", False), "personal": ("personal", False),
                "personal+blocklist": ("personal", True)}
    preds: dict[str, list[str]] = {}
    for sname, (scope, block) in settings.items():
        built = [build_case(f, pins, stance, scope, block) for f in feats]
        for method in METHODS:
            if sname == "personal+blocklist" and method in ("no-transfer", "full-transfer", "tags-only"):
                continue  # the shipped baselines ignore forbidden ids by design
            preds[f"{method} [{sname}]"] = [predict(method, tc) for tc in built]

    ci = EB.cluster_bootstrap(cases, preds, args.reps, args.seed)
    rows = []
    for name, p in preds.items():
        m = EB.metrics(gold, p)
        m = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}
        m["per_label_recall"] = {k: round(v, 3) for k, v in m["per_label_recall"].items()}
        m.update(method=name, ci95=ci[name], prediction_counts=dict(Counter(p)))
        rows.append(m)
    out = {"dataset": "carrybench_real_v1", "cases": len(cases), "label_distribution": dict(Counter(gold)),
           "label_status": "two blind LLM annotators, one model family, no human labels",
           "adapter": "label-blind, see module docstring", "bootstrap": {"reps": args.reps, "seed": args.seed},
           "results": rows,
           "per_case": [{"case_id": c["case_id"], "label": c["label"], **{n: p[i] for n, p in preds.items()}}
                        for i, c in enumerate(cases)]}
    text = json.dumps(out, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
