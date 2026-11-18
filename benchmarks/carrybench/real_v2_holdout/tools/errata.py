#!/usr/bin/env python3
"""Build and verify the erratum for line-bearing pointers that have no text hash.

The frozen v2 holdout files have ten target pointers that name a line in a config
file and carry a null `text_sha256` (nine in the scored cases, one more in the
excluded cases, all ten in the candidate file). The verifier accepts a null hash, so a wrong line
number passes. This tool records, for each of those pointers, the SHA-256 of the
span the file points at and of the span that holds the fact. It never edits the
frozen case file. Hashes use `line_span_hash` from the v1 miner.

  FILES="cases_v2_holdout_candidates.jsonl cases_v2_holdout_v1.jsonl excluded_v2_holdout.jsonl"
  python3 -I tools/errata.py build  --cases $FILES --clones-dir ./clones --out errata_v2_holdout_v1.jsonl
  python3 -I tools/errata.py verify --errata errata_v2_holdout_v1.jsonl --cases $FILES --clones-dir ./clones

Whether a recorded line holds the fact was decided by reading the pinned file
against the paraphrase. The tool cannot check that. It checks that the hashes
still match the pinned source.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mine_decisions", HERE.parents[1] / "real_v1" / "tools" / "mine_decisions.py")
md = importlib.util.module_from_spec(_spec)
sys.modules["mine_decisions"] = md
_spec.loader.exec_module(md)

FORMAT = "carrybench-real-errata-v1"

# Pointers whose recorded lines do not hold the fact, found by reading each pinned
# file. Key: (case id, path). Value: the lines that do hold it.
CORRECTIONS = {
    ("cbh-006", "pyproject.toml"): (342, 343),  # [tool.ruff.lint.pydocstyle] and convention = "google"
    ("cbh-039", "package.json"): (69, 69),      # devDependencies entry for oxfmt
}

NOTE_WRONG = ("The recorded lines hold a different setting. The fact is at the corrected lines. "
              "The frozen pointer is unchanged, and the label question is separate.")
NOTE_OK = "The recorded lines hold the fact. No change."


def read_cases(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if "case_id" in r]


def unhashed_line_pointers(case: dict) -> list[dict]:
    """Target and source pointers that name lines but carry no text hash."""
    ptrs = [case["source"]] + list(case["target"].get("evidence", []))
    return [p for p in ptrs if p.get("path") and p.get("line_start", 0) > 0 and not p.get("text_sha256")]


def _span_hash(clones: Path, repo: str, sha: str, path: str, start: int, end: int) -> str | None:
    text = md.read_blob(md.clone_path(clones, repo), sha, path)
    if text is None or not 1 <= start <= end <= len(text.splitlines()):
        return None
    return md.line_span_hash(text, start, end)


def build(cases: list[dict], clones: Path) -> list[dict]:
    out, done = [], set()
    for c in cases:
        t = c["target"]
        for p in unhashed_line_pointers(c):
            key = (c["case_id"], p["path"])
            if (key, p["line_start"], p["line_end"]) in done:  # the same case appears in more than one frozen file
                continue
            done.add((key, p["line_start"], p["line_end"]))
            cs, ce = CORRECTIONS.get(key, (p["line_start"], p["line_end"]))
            rec_hash = _span_hash(clones, t["repo"], t["sha"], p["path"], p["line_start"], p["line_end"])
            fix_hash = _span_hash(clones, t["repo"], t["sha"], p["path"], cs, ce)
            if rec_hash is None or fix_hash is None:
                raise SystemExit(f"cannot read {t['repo']}@{t['sha'][:10]}:{p['path']} for {c['case_id']}")
            out.append({
                "case_id": c["case_id"], "repo": t["repo"], "sha": t["sha"], "path": p["path"], "kind": p["kind"],
                "recorded": {"line_start": p["line_start"], "line_end": p["line_end"], "text_sha256": rec_hash},
                "status": "wrong_location" if key in CORRECTIONS else "location_confirmed",
                "corrected": {"line_start": cs, "line_end": ce, "text_sha256": fix_hash},
                "note": NOTE_WRONG if key in CORRECTIONS else NOTE_OK,
            })
    return out


APPLIES_TO = ["cases_v2_holdout_candidates.jsonl", "cases_v2_holdout_v1.jsonl", "excluded_v2_holdout.jsonl"]


def header(n: int) -> dict:
    return {"format": "carrybench-real-errata-file-header-v1", "dataset_id": "carrybench_real_v2_holdout",
            "applies_to": APPLIES_TO, "record_count": n,
            "note": "Hashes for config pointers that have lines and a null text hash in the frozen files. "
                    "The frozen files and their labels are unchanged. Records hold pointers and hashes only, no third party text."}


def check(errata: list[dict], cases: list[dict], clones: Path | None) -> list[str]:
    """Return problems. Offline checks always run, hash checks need clones."""
    problems = []
    want = {(c["case_id"], p["path"], p["line_start"], p["line_end"]) for c in cases for p in unhashed_line_pointers(c)}
    origin = {}  # what each record must say about the pointer it covers
    for c in cases:
        for p in unhashed_line_pointers(c):
            origin[(c["case_id"], p["path"], p["line_start"], p["line_end"])] = (c["target"]["repo"], c["target"]["sha"], p["kind"])
    have = {(r["case_id"], r["path"], r["recorded"]["line_start"], r["recorded"]["line_end"]) for r in errata}
    for k in sorted(want - have):
        problems.append(f"UNLISTED unhashed pointer {k}")
    for k in sorted(have - want):
        problems.append(f"STALE erratum record {k}")
    if len(have) != len(errata):
        problems.append("DUPLICATE erratum records")
    for r in errata:
        cid, rec, fix = r["case_id"], r["recorded"], r["corrected"]
        key = (cid, r["path"], rec["line_start"], rec["line_end"])
        if key in origin and origin[key] != (r["repo"], r["sha"], r["kind"]):
            problems.append(f"ORIGIN {cid} {r['path']} repo, sha or kind differs from the case pointer")
        moved = (rec["line_start"], rec["line_end"]) != (fix["line_start"], fix["line_end"])
        if (r["status"] == "wrong_location") != moved:
            problems.append(f"STATUS {cid} {r['path']} disagrees with its corrected lines")
        if (rec["text_sha256"] == fix["text_sha256"]) == moved:
            problems.append(f"HASH {cid} {r['path']} recorded and corrected hashes disagree with the status")
        if clones is not None:
            for name, span in (("recorded", rec), ("corrected", fix)):
                got = _span_hash(clones, r["repo"], r["sha"], r["path"], span["line_start"], span["line_end"])
                if got != span["text_sha256"]:
                    problems.append(f"MISMATCH {cid} {r['path']} {name} lines {span['line_start']}-{span['line_end']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    v = sub.add_parser("verify")
    for p in (b, v):
        p.add_argument("--cases", required=True, nargs="+")
        p.add_argument("--clones-dir", required=True)
    b.add_argument("--out", required=True)
    v.add_argument("--errata", required=True)
    args = ap.parse_args(argv)
    cases = [c for f in args.cases for c in read_cases(Path(f))]
    clones = Path(args.clones_dir)
    if args.cmd == "build":
        recs = build(cases, clones)
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in [header(len(recs))] + recs:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        print(json.dumps({"records": len(recs), "wrong_location": sum(r["status"] == "wrong_location" for r in recs)}))
        return 0
    errata = read_cases(Path(args.errata))
    problems = check(errata, cases, clones)
    for p in problems:
        print(p, file=sys.stderr)
    print(json.dumps({"records": len(errata), "problems": len(problems)}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
