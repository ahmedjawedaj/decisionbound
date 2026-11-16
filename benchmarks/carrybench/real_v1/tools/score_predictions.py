#!/usr/bin/env python3
"""Score one or more prediction files against CarryBench-Real v1 labels.

A prediction file is JSONL with case_id and label (extra fields are kept out of
the output). Reports the same metrics and source-clustered bootstrap intervals
as evaluate_baselines.py, plus pairwise agreement between prediction files.
No clones are needed.

  python3 -I tools/score_predictions.py --cases cases_v1.jsonl \
      --pred Z1=results/llm_zero_shot_Z1.jsonl --pred Z2=results/llm_zero_shot_Z2.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_baselines as EB  # noqa: E402


def kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--pred", action="append", required=True, help="NAME=path.jsonl, repeatable")
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    cases = EB.load_cases(Path(args.cases))
    gold = [c["label"] for c in cases]
    preds: dict[str, list[str]] = {}
    for spec in args.pred:
        name, _, path = spec.partition("=")
        by_id = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r["label"] not in EB.LABELS:
                    raise SystemExit(f"{name}: bad label {r['label']!r}")
                by_id[r["case_id"]] = r["label"]
        missing = [c["case_id"] for c in cases if c["case_id"] not in by_id]
        if missing:
            raise SystemExit(f"{name}: missing {len(missing)} cases, for example {missing[:3]}")
        preds[name] = [by_id[c["case_id"]] for c in cases]

    ci = EB.cluster_bootstrap(cases, preds, args.reps, args.seed)
    rows = []
    for name, p in preds.items():
        m = EB.metrics(gold, p)
        m = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}
        m["per_label_recall"] = {k: round(v, 3) for k, v in m["per_label_recall"].items()}
        m.update(name=name, ci95=ci[name], prediction_counts=dict(Counter(p)))
        rows.append(m)
    names = list(preds)
    pairs = {f"{a} vs {b}": {"raw": round(sum(x == y for x, y in zip(preds[a], preds[b])) / len(gold), 3),
                             "kappa": round(kappa(preds[a], preds[b]), 3)}
             for i, a in enumerate(names) for b in names[i + 1:]}
    out = {"cases": len(cases), "results": rows, "pairwise_agreement": pairs,
           "confusion": {n: {f"{g}->{p}": c for (g, p), c in sorted(Counter(zip(gold, preds[n])).items())} for n in names}}
    text = json.dumps(out, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
