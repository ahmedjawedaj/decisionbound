#!/usr/bin/env python3
"""Produce every table of the human study from one or two checked human label files.

Run this only after each human file has been sent in and has passed check_human_labels.py.
It prints a Markdown report with these parts:

  1. each human against the frozen AI pair on the scored cases
  2. each human against the second-family AI draft (all candidates)
  3. the two humans against each other (when two files are given)
  4. every case where any rater differs
  5. the stored predictions of every scored system, re-scored against human labels

Nothing is adjudicated. The adjudication rule is an owner decision and is not in this tool.
When two humans are given, the reference for part 5 is the set of cases where they gave
the same four-way label. With one human it is that human's labels, and the report says so.
The frozen AI labels, the AI draft and the stored system predictions are only read.

    python3 -I analyse_human_study.py --human labels_H1.jsonl [--human labels_H2.jsonl] [--out report.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOLDOUT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HOLDOUT.parent / "real_v1" / "tools"))
import check_human_labels as checker  # noqa: E402
import compare_labels as cmp  # noqa: E402
import evaluate_baselines as ev  # noqa: E402

LABELS = ev.LABELS
SPECIAL = checker.SPECIAL
DEFAULT_AI_DRAFT = HOLDOUT / "annotations" / "ai_other_family" / "labels_gpt6_ai_draft_v1_1.jsonl"
DEFAULT_RESULTS = (HOLDOUT / "results" / "baselines_v2_holdout.json",
                   HOLDOUT / "results" / "decisionbound_v2_holdout.json")


def _esc(text) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def reference_labels(humans: list[tuple[dict, dict[str, dict]]]) -> tuple[dict[str, str], str, list[str], list[str]]:
    """Return (reference label per case, how it was built, disputed ids, ids left out for a special label).

    One human: that human's four-way labels. Two humans: only cases where both gave the same
    four-way label. invalid and no_signal never enter the reference because they have no
    four-way meaning to score a system against.
    """
    ids = sorted(set.intersection(*(set(labels) for _, labels in humans)))
    ref: dict[str, str] = {}
    disputed: list[str] = []
    special: list[str] = []
    for cid in ids:
        got = [labels[cid]["label"] for _, labels in humans]
        if any(g in SPECIAL for g in got):
            special.append(cid)
        elif len(set(got)) > 1:
            disputed.append(cid)
        else:
            ref[cid] = got[0]
    how = ("the labels of the single human" if len(humans) == 1
           else "the cases where both humans gave the same four-way label")
    return ref, how, disputed, special


def load_systems(paths: list[Path]) -> list[tuple[str, list[str], list[dict]]]:
    """Read result files. System names come from the file's own `results` section."""
    out = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        names = sorted({r.get("baseline") or r.get("method") for r in data["results"]} - {None})
        out.append((Path(p).name, names, data["per_case"]))
    return out


def check_result_shape(fname: str, systems: list[str], rows: list[dict], scored: dict[str, dict]) -> None:
    """Refuse a result file that does not line up with the scored cases, instead of scoring part of it."""
    ids = [r["case_id"] for r in rows]
    dupes = sorted(c for c, n in Counter(ids).items() if n > 1)
    if dupes:
        raise SystemExit(f"{fname} lists a case more than once: {dupes[:5]}")
    unknown = sorted(set(ids) - set(scored))
    if unknown:
        raise SystemExit(f"{fname} has predictions for cases that are not in the scored file: {unknown[:5]}")
    absent = sorted(set(scored) - set(ids))
    if absent:
        raise SystemExit(f"{fname} has no predictions for {len(absent)} scored case(s): {absent[:5]}")
    if not systems:
        raise SystemExit(f"{fname} names no systems in its results section")
    for r in rows:
        gone = [s for s in systems if s not in r]
        if gone:
            raise SystemExit(f"{fname} has no prediction by {gone[:3]} for {r['case_id']}")


def rescore(per_case_sets: list[tuple[str, list[str], list[dict]]], ref: dict[str, str],
            scored: dict[str, dict], reps: int, seed: int) -> list[str]:
    out: list[str] = []
    for fname, systems, all_rows in per_case_sets:
        check_result_shape(fname, systems, all_rows, scored)
        rows = [r for r in all_rows if r["case_id"] in ref]
        if not rows:
            out += [f"No case in `{fname}` has a reference label, so nothing was re-scored.", ""]
            continue
        gold_h = [ref[r["case_id"]] for r in rows]
        gold_ai = [scored[r["case_id"]]["label"] for r in rows]
        preds = {s: [r[s] for r in rows] for s in systems}
        cases_for_ci = [{"label": g, "source": scored[r["case_id"]]["source"]} for g, r in zip(gold_h, rows)]
        ci = ev.cluster_bootstrap(cases_for_ci, preds, reps, seed)
        out += [f"### Systems in `{fname}`", "",
                f"{len(rows)} cases. Label counts against the human reference: "
                + ", ".join(f"{k} {v}" for k, v in sorted(Counter(gold_h).items())) + ". "
                f"The AI columns use the frozen AI label of the same {len(rows)} cases, so the two accuracy columns are comparable. "
                "Macro F1 counts a label that is absent from the reference as 0, so a system can score below 1 even when every prediction is right.", "",
                "| system | accuracy vs AI labels | accuracy vs human | difference | macro F1 vs human | 95% interval (accuracy vs human) |",
                "|---|---|---|---|---|---|"]
        for s in systems:
            acc_ai = ev.metrics(gold_ai, preds[s])["accuracy"]
            m = ev.metrics(gold_h, preds[s])
            lo, hi = ci[s]["accuracy"]
            out.append(f"| {_esc(s)} | {acc_ai:.3f} | {m['accuracy']:.3f} | {m['accuracy'] - acc_ai:+.3f} | "
                       f"{m['macro_f1']:.3f} | {lo:.3f} to {hi:.3f} |")
        out.append("")
    return out


def report(humans, draft, scored, excluded, per_case_sets, reps, seed) -> str:
    out = ["# Human study analysis", "",
           "Generated by `analyse_human_study.py`. Nothing here is adjudicated. The frozen AI labels, the AI draft and "
           "the stored system predictions are unchanged.", ""]
    for header, _ in humans:
        if header.get("saw_ai_labels") is not False:
            out += [f"**Warning: {header.get('annotator_id', '?')} declared `saw_ai_labels` = {header.get('saw_ai_labels')}. "
                    "This file is not blind, so its agreement with the AI labels is not independent.**", ""]

    # 1 and 3 reuse the existing comparison so the numbers cannot drift from compare_labels.py
    body = re.sub(r"^## ", "### ", cmp.report(humans, scored, excluded).split("\n", 4)[4], flags=re.M)
    out += ["## " + ("Parts 1 and 3: against the frozen AI pair and against each other" if len(humans) == 2
                      else "Part 1: against the frozen AI pair"), "", body, ""]

    # 2 second-family AI draft
    d_header, d_labels = draft
    out += [f"## Part 2: against the second-family AI draft ({d_header.get('annotator_id', '?')})", "",
            "The draft is an AI pass from packet paraphrases. It is not human and it was not blind in the preregistration sense.", ""]
    for header, labels in humans:
        ids = sorted(set(labels) & set(d_labels))
        pairs = [(d_labels[c]["label"], labels[c]["label"]) for c in ids]
        out += [f"{header.get('annotator_id', '?')} against the draft on {len(ids)} cases: " + cmp.fmt(cmp.summarise(pairs)), ""]

    # 4 every contested case
    out += ["## Part 4: every case where any rater differs", ""]
    ids = sorted(set.intersection(*(set(l) for _, l in humans)))
    head = ["case"] + [_esc(h.get("annotator_id", "?")) for h, _ in humans] + ["AI A", "AI B", "AI draft"]
    rows = []
    for cid in ids:
        ai = scored[cid]["label"] if cid in scored else None
        prov = excluded[cid].get("label_provenance", {}).get("labels", {}) if cid in excluded else {}
        a = ai if cid in scored else prov.get("A_v1_1_blind")
        b = ai if cid in scored else prov.get("B_v1_1_blind")
        raters = [labels[cid]["label"] for _, labels in humans] + [a, b, d_labels[cid]["label"] if cid in d_labels else None]
        if len({r for r in raters if r is not None}) > 1:
            rows.append([cid] + [("none" if r is None else r) for r in raters])
    if rows:
        out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        out += ["| " + " | ".join(_esc(x) for x in r) + " |" for r in rows]
        out.append("")
    else:
        out += ["No rater differs from any other on any case.", ""]

    # 5 re-score
    ref, how, disputed, special = reference_labels(humans)
    out += ["## Part 5: stored system predictions re-scored against human labels", "",
            f"The reference is {how}. It holds {len(ref)} of {len(ids)} cases."]
    if disputed:
        out += [f"{len(disputed)} case(s) are left out because the humans disagree and no adjudication has been done: {', '.join(disputed)}."]
    if special:
        out += [f"{len(special)} case(s) are left out because a human marked them invalid or no_signal: {', '.join(special)}."]
    no_pred = sorted(c for c in ref if c not in scored)
    if no_pred:
        out += [f"{len(no_pred)} reference case(s) have no stored system prediction because the AI pair did not agree on them: {', '.join(no_pred)}."]
    out += ["", "Predictions come from the stored result files. No system was run again. The interval resamples whole "
            f"source repositories ({reps} resamples, seed {seed}).",
            "`family_prior_loo` was built from the AI labels, and `precedence_authority` reads the case family field. "
            "Neither is an independent policy, so read their human scores as checks on the labels and not as results.", ""]
    out += rescore(per_case_sets, ref, scored, reps, seed)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--human", action="append", required=True, help="a checked human label file, once or twice")
    ap.add_argument("--ai-draft", default=str(DEFAULT_AI_DRAFT))
    ap.add_argument("--results", action="append", help="stored result file with per_case predictions (default: the two holdout files)")
    ap.add_argument("--scored", default=str(HOLDOUT / "cases_v2_holdout_v1.jsonl"))
    ap.add_argument("--cases", default=str(HOLDOUT / "cases_v2_holdout_candidates.jsonl"))
    ap.add_argument("--excluded", default=str(HOLDOUT / "excluded_v2_holdout.jsonl"))
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="", help="also write the report to this new file")
    ap.add_argument("--force", action="store_true", help="let --out replace an existing file that is not an input")
    args = ap.parse_args(argv)
    if len(args.human) > 2:
        ap.error("give one or two human files")
    if args.reps < 1:
        ap.error("--reps must be at least 1")
    results = [Path(p) for p in (args.results or DEFAULT_RESULTS)]
    inputs = [Path(p) for p in (*args.human, args.ai_draft, args.scored, args.cases, args.excluded)] + results
    for p in inputs:
        if not p.is_file():
            ap.error(f"cannot read {p}")
    if args.out:
        out_path = Path(args.out).resolve()
        if out_path in {p.resolve() for p in inputs}:
            ap.error("--out is one of the input files and would overwrite it")
        if HOLDOUT.resolve() in out_path.parents:
            ap.error("--out is inside the holdout directory. Write the report somewhere else so the frozen files stay as they are")
        if out_path.exists() and not args.force:
            ap.error("--out already exists. Pick a new name or pass --force")
    ids = checker._case_ids(args.cases)
    humans = [cmp.load_human(Path(p), ids) for p in args.human]
    who = [h.get("annotator_id") for h, _ in humans]
    if len(set(who)) != len(who):
        ap.error(f"the two human files use the same annotator_id ({who[0]}). Each human needs their own id")
    if len(humans) == 2:
        (_, l1), (_, l2) = humans
        same = sum(1 for c in l1 if l1[c].get("rationale", "").strip() == l2[c].get("rationale", "").strip())
        if same * 2 >= len(l1):
            ap.error(f"the two human files give the same rationale on {same} of {len(l1)} cases, so one looks like a copy of the other. "
                     "Each human must label the packet on their own")
    draft = cmp.load_human(Path(args.ai_draft), ids)
    text = report(humans, draft, cmp.load_cases(Path(args.scored)), cmp.load_cases(Path(args.excluded)),
                  load_systems(results), args.reps, args.seed)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
