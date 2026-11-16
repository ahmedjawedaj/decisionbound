#!/usr/bin/env python3
"""Score simple baselines on CarryBench-Real v1.

Standard library only. Needs read access to the pinned clones (see
fetch_pins.py) so that baselines can read the text that the case pointers
name. It never executes third party code and never writes third party text.

Labels are the agreed labels of two blind LLM annotators. They are not human
labels and not ground truth. Read AGREEMENT_REPORT.md before citing a score.

Baselines
  majority            always the most common label of the file
  always_apply        always apply
  keyword_jaccard     apply when source and target evidence share vocabulary
  shared_tool         apply when both texts name a tool of the same family
  config_lookup       apply when a configured target tool matches the source
  precedence_rule     a Mneme style rule: the target's own recorded rule wins,
                      otherwise apply. Cannot emit translate or escalate
  precedence_authority  precedence_rule plus escalate for authority families
                      (uses the case family field, so it is NOT leak free)
  family_prior_loo    leave one out majority label per family. A leakage
                      detector for the family field, not a real policy

Metrics: exact accuracy, macro F1 over the four labels, transfer versus
withhold accuracy, harmful apply rate (apply where the label is abstain or
escalate), harmful transfer rate (apply or translate there) and missed transfer rate (withhold where the label is apply or
translate). Confidence intervals come from a bootstrap that resamples whole
source repositories, because cases from one source are not independent.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mine_decisions as M  # noqa: E402

LABELS = ("apply", "translate", "abstain", "escalate")
TRANSFER = {"apply", "translate"}
AUTHORITY_FAMILIES = {
    "licensing", "contribution_license_terms", "contributor_license_agent", "dco_signoff",
    "security_reporting", "agent_public_actions", "ai_coauthor_trailer", "ai_disclosure",
    "practice_ai_disclosure", "release_publishing",
}
INSTRUCTION_FILES = ("agents.md", "claude.md", "contributing", "/adr", "adr/", "decisions/")


def load_cases(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("format", "").startswith("carrybench-real-case") and r.get("label")]


def texts(case: dict, clones: Path) -> tuple[str, str]:
    return M._case_texts(case, clones)


class Features:
    """Everything a baseline may look at, computed once per case."""

    def __init__(self, case: dict, clones: Path, stance: dict):
        self.case = case
        self.src, self.tgt = texts(case, clones)
        self.jaccard = M.jaccard(M.tokens(self.src), M.tokens(self.tgt))
        self.src_mentions = M.classify_mentions(self.src)
        self.tgt_mentions = M.classify_mentions(self.tgt)
        self.shared_tool = bool({(f, m) for f, m, _ in self.src_mentions} & {(f, m) for f, m, _ in self.tgt_mentions})
        want = {(f, m) for f, m, p in self.src_mentions if p == "prescribe" and not f.startswith("practice_")}
        self.config_match = bool(want & stance.get(case["target"]["repo"], set()))
        self.kinds = [e["kind"] for e in case["target"].get("evidence", [])]
        self.paths = [(e.get("path") or "").lower() for e in case["target"].get("evidence", [])]
        self.family = case["family"]


def b_majority(label: str):
    return lambda f: label


def b_always_apply(f: Features) -> str:
    return "apply"


def b_jaccard(f: Features) -> str:
    return "apply" if f.jaccard >= 0.15 else "abstain"


def b_shared_tool(f: Features) -> str:
    return "apply" if f.shared_tool else "abstain"


def b_config_lookup(f: Features) -> str:
    return "apply" if f.config_match else "abstain"


def b_precedence(f: Features) -> str:
    src_fams = {fam for fam, _m, _p in f.src_mentions}
    tgt_by_fam: dict[str, set[str]] = {}
    for fam, m, p in f.tgt_mentions:
        if p == "prescribe":
            tgt_by_fam.setdefault(fam, set()).add(m)
    prescribed = {(fam, m) for fam, m, p in f.src_mentions if p == "prescribe"}
    if src_fams:
        topics = [fam for fam in src_fams if fam in tgt_by_fam or any(k.startswith("config_fact") for k in f.kinds)]
        if topics:
            agree = all(any((fam, m) in prescribed for m in tgt_by_fam.get(fam, set())) for fam in topics if fam in tgt_by_fam)
            return "apply" if agree and any(fam in tgt_by_fam for fam in topics) else "abstain"
        return "apply"
    has_local_rules = any(any(tag in p for tag in INSTRUCTION_FILES) for p in f.paths)
    return "abstain" if has_local_rules else "apply"


def b_precedence_authority(f: Features) -> str:
    if f.family in AUTHORITY_FAMILIES:
        return "escalate"
    return b_precedence(f)


def macro_f1(gold: list[str], pred: list[str]) -> float:
    scores = []
    for lab in LABELS:
        tp = sum(g == lab and p == lab for g, p in zip(gold, pred))
        fp = sum(g != lab and p == lab for g, p in zip(gold, pred))
        fn = sum(g == lab and p != lab for g, p in zip(gold, pred))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(scores) / len(scores)


def metrics(gold: list[str], pred: list[str]) -> dict:
    n = len(gold)
    harm_den = sum(g in ("abstain", "escalate") for g in gold)
    miss_den = sum(g in TRANSFER for g in gold)
    return {
        "n": n,
        "accuracy": sum(g == p for g, p in zip(gold, pred)) / n,
        "macro_f1": macro_f1(gold, pred),
        "transfer_vs_withhold": sum((g in TRANSFER) == (p in TRANSFER) for g, p in zip(gold, pred)) / n,
        "harmful_apply_rate": (sum(p == "apply" and g in ("abstain", "escalate") for g, p in zip(gold, pred)) / harm_den) if harm_den else 0.0,
        "harmful_transfer_rate": (sum(p in TRANSFER and g in ("abstain", "escalate") for g, p in zip(gold, pred)) / harm_den) if harm_den else 0.0,
        "missed_transfer_rate": (sum(p not in TRANSFER and g in TRANSFER for g, p in zip(gold, pred)) / miss_den) if miss_den else 0.0,
        "per_label_recall": {lab: (sum(g == lab and p == lab for g, p in zip(gold, pred)) / max(1, sum(g == lab for g in gold))) for lab in LABELS},
    }


def cluster_bootstrap(cases: list[dict], preds: dict[str, list[str]], reps: int, seed: int) -> dict:
    rng = random.Random(seed)
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(cases):
        groups.setdefault(c["source"]["repo"], []).append(i)
    keys = sorted(groups)
    out: dict[str, dict[str, list[float]]] = {name: {"accuracy": [], "macro_f1": []} for name in preds}
    for _ in range(reps):
        idx = [i for k in (rng.choice(keys) for _ in keys) for i in groups[k]]
        gold = [cases[i]["label"] for i in idx]
        for name, p in preds.items():
            pp = [p[i] for i in idx]
            out[name]["accuracy"].append(sum(g == x for g, x in zip(gold, pp)) / len(idx))
            out[name]["macro_f1"].append(macro_f1(gold, pp))
    ci = {}
    for name, d in out.items():
        ci[name] = {k: [round(sorted(v)[int(0.025 * reps)], 3), round(sorted(v)[int(0.975 * reps) - 1], 3)] for k, v in d.items()}
    return ci


def load_stance(mined: Path | None) -> dict:
    stance: dict[str, set[tuple[str, str]]] = {}
    if mined and mined.exists():
        for line in mined.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("kind") == "config_fact":
                for f in r["families"]:
                    stance.setdefault(r["repo"], set()).add((f["family"], f["member"]))
    return stance


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--clones-dir", required=True)
    ap.add_argument("--mined", default="", help="mined JSONL, enables config_lookup")
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    clones = Path(args.clones_dir)
    stance = load_stance(Path(args.mined) if args.mined else None)
    feats = [Features(c, clones, stance) for c in cases]
    gold = [c["label"] for c in cases]
    majority = Counter(gold).most_common(1)[0][0]

    fam_counts: dict[str, Counter] = {}
    for c in cases:
        fam_counts.setdefault(c["family"], Counter())[c["label"]] += 1

    def family_prior(i: int) -> str:
        c = cases[i]
        cnt = fam_counts[c["family"]].copy()
        cnt[c["label"]] -= 1
        if sum(cnt.values()) <= 0:
            return Counter(gold[:i] + gold[i + 1:]).most_common(1)[0][0]
        return cnt.most_common(1)[0][0]

    baselines = {
        f"majority({majority})": [b_majority(majority)(f) for f in feats],
        "always_apply": [b_always_apply(f) for f in feats],
        "keyword_jaccard>=0.15": [b_jaccard(f) for f in feats],
        "shared_tool": [b_shared_tool(f) for f in feats],
        "precedence_rule": [b_precedence(f) for f in feats],
        "precedence_authority": [b_precedence_authority(f) for f in feats],
        "family_prior_loo": [family_prior(i) for i in range(len(cases))],
    }
    if stance:
        baselines["config_lookup"] = [b_config_lookup(f) for f in feats]

    ci = cluster_bootstrap(cases, baselines, args.reps, args.seed)
    results = []
    for name, pred in baselines.items():
        m = metrics(gold, pred)
        m = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}
        m["per_label_recall"] = {k: round(v, 3) for k, v in m["per_label_recall"].items()}
        m["baseline"] = name
        m["ci95"] = ci[name]
        m["prediction_counts"] = dict(Counter(pred))
        results.append(m)
    out = {
        "dataset": "carrybench_real_v1",
        "label_status": "two blind LLM annotators, one model family, no human labels",
        "cases": len(cases),
        "label_distribution": dict(Counter(gold)),
        "bootstrap": {"reps": args.reps, "seed": args.seed, "cluster": "source repository"},
        "results": results,
        "per_case": [{"case_id": c["case_id"], "label": c["label"], **{n: p[i] for n, p in baselines.items()}}
                     for i, c in enumerate(cases)],
    }
    text = json.dumps(out, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
