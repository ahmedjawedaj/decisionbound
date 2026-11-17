#!/usr/bin/env python3
"""Draw the unlabelled CarryBench-Real v2 holdout shortlist.

Pointer-only output. Reuses the v1 miner (mine_decisions.py) for statements,
config facts and cross-repository pairs, then draws a seeded, stratified sample.
No label of any kind is read, written or implied. Strata are structural
(what kind of evidence the target holds), not predictions of the right action.

  python3 -I tools/shortlist_candidates.py shortlist --pins pins.csv --clones-dir ./clones \
      --mined mined.jsonl --out shortlist.jsonl --seed 20261117

`mined.jsonl` comes from `mine_decisions.py mine` run over the pinned clones.
The target stance of each repository is rebuilt here from the mined config facts and statements. `view` prints a span for local curation only; its
output holds third party text and must never be stored or released.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_TOOLS = HERE.parents[1] / "real_v1" / "tools"
V1_PINS = HERE.parents[1] / "real_v1" / "pins.csv"
_spec = importlib.util.spec_from_file_location("mine_decisions", V1_TOOLS / "mine_decisions.py")
md = importlib.util.module_from_spec(_spec)
sys.modules["mine_decisions"] = md
_spec.loader.exec_module(md)

DOC_CLASSES = ("agent_instructions", "adr", "contributing", "rfc")
MAX_SPAN_CHARS = 380
# A tool statement counts as a candidate decision only when it carries choice language, not just a command.
CHOICE_CUE = re.compile(r"\b(use|uses|using|only|never|instead of|rather than|prefer\w*|always|not use|"
                        r"package manager|test runner|formatter|linter|toolchain|we use|must use|do not use|don't use)\b", re.I)
CHECKLIST_CUE = re.compile(r"\b(must (pass|succeed|report)|should pass|runnable with|passes?)\b", re.I)
BEFORE_CUE = re.compile(r"\b(use|uses|using|we use|always use|only use|prefer\w*|instead of|rather than|not use|never use|"
                        r"do not use|don't use|must use|exclusively|stick to|standardi[sz]e on|migrate\w* to)\W+(\w+\W+){0,4}$", re.I)
AFTER_CUE = re.compile(r"^\W*(\w+\W+){0,3}(is|are|as) (the |our |a )?(required|only|default|standard|preferred|package manager|test runner|"
                       r"formatter|linter|toolchain)|^\W*(only|exclusively)\b|^\W*is (required|banned|forbidden|not)", re.I)


def tool_choice_statement(text: str) -> bool:
    """True when a tool family member is named inside explicit choice language, not just a command."""
    for fam, members in md.FAMILY_RX.items():
        if fam.startswith("practice_"):
            continue
        for rx in members.values():
            for m in rx.finditer(text):
                if BEFORE_CUE.search(text[max(0, m.start() - 70):m.start()]) or AFTER_CUE.search(text[m.end():m.end() + 60]):
                    return True
    return False
MIN_SPAN_CHARS = 30

# Authority and practice topics the v1 miner families do not cover.
TOPICS: dict[str, re.Pattern] = {
    "ai_disclosure": re.compile(
        r"\b(AI|LLM|agent|Claude|Copilot|Codex|Cursor)\b.{0,70}\b(disclos\w+|attribut\w+|label\w*|mention\w*|declare\w*)\b"
        r"|\bdisclos\w+\b.{0,70}\b(AI|LLM)\b", re.I),
    "ai_coauthor_trailer": re.compile(r"co-authored-by", re.I),
    "dco_signoff": re.compile(r"signed-off-by|\bDCO\b|git commit -s\b|developer certificate of origin", re.I),
    "contributor_license_agent": re.compile(r"\bCLA\b|contributor licen[sc]e agreement", re.I),
    "security_reporting": re.compile(
        r"\b(security (vulnerabilit\w+|issues?|bugs?|reports?|problems?|flaws?)|vulnerabilit\w+)\b"
        r".{0,90}\b(public\w*|private\w*|advisor\w+|report\w*|disclos\w+|SECURITY\.md)\b", re.I),
    "agent_public_actions": re.compile(
        r"\b(do not|never|don't|must not|should not|without (asking|permission|approval|confirmation|being asked))\b"
        r".{0,70}\b(push|post|comment|open (a |an )?(pull request|PR|issue)|create (a |an )?(pull request|PR|issue)"
        r"|merge|publish|close|reply|submit|commit)\b", re.I),
    "release_publishing": re.compile(
        r"\b(npm publish|twine upload|publish(ing)? (a |the |to )|release (workflow|process|notes)|version bump|bump(ing)? (the )?version|cut(ting)? a release)\b", re.I),
    "licensing": re.compile(
        r"\b(licen[sc]e headers?|SPDX|copyright (header|notice|holder)|third[- ]party (code|licen[sc]e)|copy(ing)? code|copyleft|\bGPL\b)\b", re.I),
}
TOPIC_NEGATIVE_OK = {"agent_public_actions"}
TOPIC_ONLY_AGENT = {"agent_public_actions"}

# stratum -> quota. Structural strata only.
QUOTAS = {
    "A_tool_target_config_same": 5,
    "B_tool_target_config_differs": 5,
    "C_tool_target_text_differs": 3,
    "D_tool_mixed_signals": 2,
    "E_change_record": 3,
    "F_practice": 8,
    "G_authority_target_has_text": 8,
    "H_authority_target_silent": 6,
}
AUTHORITY_TOPIC_QUOTA = {  # across G and H together
    "ai_disclosure": 3, "ai_coauthor_trailer": 1, "dco_signoff": 2, "contributor_license_agent": 1,
    "security_reporting": 2, "agent_public_actions": 3, "release_publishing": 1, "licensing": 1,
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def h(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def span(clones: Path, pin: dict, path: str, a: int, b: int) -> str:
    text = md.read_blob(md.clone_path(clones, pin["repo"]), pin["sha"], path)
    return "\n".join((text or "").splitlines()[a - 1:b])


def lang_ok(family: str, inv: dict) -> bool:
    if family.startswith("js_") or family in ("change_record",):
        return inv["n_ts"] > 0 or inv["js"]
    if family.startswith("py_"):
        return inv["n_py"] > 0
    return True


def ptr(r: dict) -> dict:
    """Pointer record for a mined statement or config fact. Paraphrase is added by curation."""
    kind = "text" if r["kind"] != "config_fact" else f"config_fact:{r['how']}"
    return {"kind": kind, "path": r["path"], "line_start": r["line_start"], "line_end": r["line_end"],
            "text_sha256": r["text_sha256"], "paraphrase": ""}


def topic_statements(clones: Path, pins: dict[str, dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for repo in sorted(pins):
        pin = pins[repo]
        tree = md.list_tree(md.clone_path(clones, repo), pin["sha"])
        for path, cls in sorted((p, md.signal_class(p)) for p, _ in tree):
            if cls not in DOC_CLASSES:
                continue
            text = md.read_blob(md.clone_path(clones, repo), pin["sha"], path)
            if not text:
                continue
            for a, b, joined, in_code in md.iter_statements(text):
                if in_code or not (MIN_SPAN_CHARS <= len(joined) <= MAX_SPAN_CHARS) or b - a > 3:
                    continue
                for topic, rx in TOPICS.items():
                    if not rx.search(joined):
                        continue
                    if topic in TOPIC_ONLY_AGENT and cls != "agent_instructions":
                        continue
                    if topic not in ("ai_coauthor_trailer", "dco_signoff", "contributor_license_agent") \
                            and not md.NORMATIVE.search(joined):
                        continue
                    out[topic].append({"repo": repo, "sha": pin["sha"], "path": path, "line_start": a, "line_end": b,
                                       "signal_class": cls,
                                       "text_sha256": md.sha256_text(md.norm_text(joined)),
                                       "kind": "statement", "how": ""})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("shortlist")
    for a in ("--pins", "--clones-dir", "--mined", "--out"):
        p.add_argument(a, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--oversample", type=int, default=3, help="shortlist this many candidates per quota slot")
    p = sub.add_parser("view", help="LOCAL ONLY: print a span's text for curation")
    p.add_argument("--pins", required=True); p.add_argument("--clones-dir", required=True)
    p.add_argument("--repo", required=True); p.add_argument("--path", required=True)
    p.add_argument("--start", type=int, required=True); p.add_argument("--end", type=int, required=True)
    args = ap.parse_args(argv)

    pins = {r["repo"]: r for r in csv.DictReader(open(args.pins, encoding="utf-8"))}
    clones = Path(args.clones_dir)
    if args.cmd == "view":
        print(span(clones, pins[args.repo], args.path, args.start, args.end))
        return 0

    v1 = {r["repo"].lower() for r in csv.DictReader(open(V1_PINS, encoding="utf-8"))}
    clash = sorted(r for r in pins if r.lower() in v1)
    if clash:
        raise SystemExit(f"repositories already in real_v1 pins: {clash}")
    inv: dict[str, dict] = {}
    for r in pins.values():
        tree = md.list_tree(md.clone_path(clones, r["repo"]), r["sha"])
        names = {p for p, _ in tree}
        inv[r["repo"]] = {"n_py": int(r["n_py"]), "n_ts": int(r["n_ts"]), "js": "package.json" in names}

    rng = random.Random(args.seed)
    mined = [m for m in read_jsonl(Path(args.mined)) if m["repo"] in pins]
    by_repo_family: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in mined:
        if m["kind"] in ("config_fact", "statement"):
            for f in m["families"]:
                by_repo_family[(m["repo"], f["family"])].append(m)

    def evidence_for(tgt: str, family: str, lead: dict) -> list[dict]:
        ev = [ptr(lead)]
        others = sorted((m for m in by_repo_family.get((tgt, family), []) if m["id"] != lead["id"]),
                        key=lambda m: (m["kind"] != "config_fact", m["kind"] == "statement" and lead["kind"] == "statement", m["id"]))
        want_other = "config_fact" if lead["kind"] != "config_fact" else "statement"
        for m in others:
            if m["kind"] == want_other:
                ev.append(ptr(m))
                break
        return ev

    # target stance per (repo, family): T1 config members and T2/T3 text members with polarity
    stance: dict[tuple[str, str], dict] = {}
    for (repo, fam), recs in by_repo_family.items():
        st = {"t1": set(), "text": {}, "recs": recs}
        for m in recs:
            for f in m["families"]:
                if f["family"] != fam:
                    continue
                if m["kind"] == "config_fact":
                    st["t1"].add(f["member"])
                elif m["signal_class"] in DOC_CLASSES:
                    st["text"].setdefault(f["member"], set()).add(f["polarity"])
        stance[(repo, fam)] = st
    by_id = {m["id"]: m for m in mined}
    tgt_by_family: dict[str, list[str]] = defaultdict(list)
    for (repo, fam) in sorted(stance):
        tgt_by_family[fam].append(repo)

    def classify(fam: str, mem: str, pol: str, st: dict) -> str | None:
        """Structural stratum from the target's recorded state alone. This is not a label."""
        single = len(md.FAMILIES[fam]) == 1
        if fam.startswith("practice_"):
            if not any(m["kind"] == "statement" for m in st["recs"]):
                return None
            return "F_practice"
        text_prescribed = {k for k, v in st["text"].items() if "prescribe" in v}
        if fam == "change_record":
            return "E_change_record" if st["t1"] or text_prescribed else None
        if st["t1"] and text_prescribed and not (st["t1"] & text_prescribed):
            return "D_tool_mixed_signals"
        if st["t1"]:
            agrees = (mem in st["t1"]) == (pol == "prescribe")
            return "A_tool_target_config_same" if agrees else "B_tool_target_config_differs"
        if text_prescribed and not single:
            return "C_tool_target_text_differs" if (mem not in text_prescribed) == (pol == "prescribe") else None
        return None

    pool: dict[str, list[dict]] = defaultdict(list)
    for m in mined:
        if m["kind"] != "statement" or m["signal_class"] not in DOC_CLASSES or len(m["families"]) != 1:
            continue
        if m["line_end"] - m["line_start"] > 3:
            continue
        f = m["families"][0]
        fam, mem, pol = f["family"], f["member"], f["polarity"]
        if fam == "practice_ai_disclosure":
            continue  # covered by the authority strata
        stext = span(clones, pins[m["repo"]], m["path"], m["line_start"], m["line_end"])
        if not (MIN_SPAN_CHARS <= len(stext) <= MAX_SPAN_CHARS):
            continue
        if len({(a, b) for a, b, _ in md.classify_mentions(stext)}) != 1:  # atomic span
            continue
        if not fam.startswith("practice_") and (not tool_choice_statement(stext) or CHECKLIST_CUE.search(stext)):
            continue
        pool["_src"].append({"m": m, "fam": fam, "mem": mem, "pol": pol})
    rng.shuffle(pool["_src"])
    # for each source statement pick, in seeded order, the first target repo per stratum
    for item in pool["_src"]:
        m, fam, mem, pol = item["m"], item["fam"], item["mem"], item["pol"]
        targets = sorted((r for r in tgt_by_family[fam] if r != m["repo"]),
                         key=lambda r: h(args.seed, m["id"], r))
        seen_strata: set[str] = set()
        for r in targets:
            ok_lang = lang_ok(fam, inv[r])
            stratum = classify(fam, mem, pol, stance[(r, fam)])
            if stratum is None:
                continue
            # tool strata: target ecosystem must fit, except B keeps a few deliberate cross-ecosystem draws
            key = (stratum, ok_lang)
            if key in seen_strata:
                continue
            seen_strata.add(key)
            pool[stratum].append({"stratum": stratum, "family": fam, "cross_ecosystem": not ok_lang, "relation_proxy": "n/a",
                                  "source": {"repo": m["repo"], "path": m["path"], "line_start": m["line_start"],
                                             "line_end": m["line_end"], "text_sha256": m["text_sha256"],
                                             "signal_class": m["signal_class"]},
                                  "target_repo": r, "fam": fam, "te_lead": stance[(r, fam)]["recs"]})
    del pool["_src"]
    chosen: list[dict] = []
    used_src_span: set[tuple] = set()
    src_n: Counter = Counter()
    tgt_n: Counter = Counter()
    fam_n: Counter = Counter()

    def take(c: dict) -> bool:
        s = c["source"]
        key = (s["repo"], s["path"], s["line_start"])
        if key in used_src_span or src_n[s["repo"]] >= 2 or tgt_n[c["target_repo"]] >= 2:
            return False
        if s["repo"] == c["target_repo"] or fam_n[c["family"]] >= 7:
            return False
        used_src_span.add(key); src_n[s["repo"]] += 1; tgt_n[c["target_repo"]] += 1; fam_n[c["family"]] += 1
        return True

    for stratum in ("A_tool_target_config_same", "B_tool_target_config_differs", "C_tool_target_text_differs",
                    "D_tool_mixed_signals", "E_change_record", "F_practice"):
        cand = [c for c in pool[stratum] if not c["cross_ecosystem"]]
        cross = [c for c in pool[stratum] if c["cross_ecosystem"]]
        rng.shuffle(cand); rng.shuffle(cross)
        quota = QUOTAS[stratum] * args.oversample
        if stratum == "B_tool_target_config_differs":
            order = cross[:2 * args.oversample] + cand  # a few deliberately outside the source's ecosystem
        else:
            order = cand
        n = 0
        for c in order:
            if n >= quota:
                break
            if take(c):
                recs = sorted(c["te_lead"], key=lambda x: (x["kind"] != "config_fact" if c["stratum"].startswith(("A", "B", "D")) else x["kind"] == "config_fact", x["id"]))
                lead = recs[0]
                chosen.append({"stratum": stratum, "family": c["family"], "cross_ecosystem": c["cross_ecosystem"],
                               "relation_proxy": c["relation_proxy"],
                               "source": {k: c["source"][k] for k in ("repo", "path", "line_start", "line_end", "text_sha256", "signal_class")},
                               "target_repo": c["target_repo"], "target_evidence": evidence_for(c["target_repo"], c["fam"], lead)})
                n += 1

    # authority strata
    ts = topic_statements(clones, pins)
    has_topic = {(topic, x["repo"]) for topic, xs in ts.items() for x in xs}
    for topic, quota in AUTHORITY_TOPIC_QUOTA.items():
        srcs = sorted(ts.get(topic, []), key=lambda x: h(args.seed, topic, x["repo"], x["path"], x["line_start"]))
        placed = 0
        want = quota * args.oversample
        for i, s in enumerate(srcs):
            if placed >= want:
                break
            key = (s["repo"], s["path"], s["line_start"])
            if key in used_src_span or src_n[s["repo"]] >= 2:
                continue
            silent_turn = (placed % 3 == 2)  # about one third of the slots use a target silent on the topic
            targets = sorted((r for r in pins if r != s["repo"] and tgt_n[r] < 2 and
                              (((topic, r) not in has_topic) == silent_turn)),
                             key=lambda r: h(args.seed, topic, s["repo"], r))
            if silent_turn:  # a silent target must still publish contributor guidance of some kind
                targets = [r for r in targets if any(md.signal_class(p) for p, _ in md.list_tree(md.clone_path(clones, r), pins[r]["sha"]))]
            if not targets:
                continue
            tgt = targets[0]
            used_src_span.add(key); src_n[s["repo"]] += 1; tgt_n[tgt] += 1
            ev: list[dict] = []
            if not silent_turn:
                cands = sorted((x for x in ts[topic] if x["repo"] == tgt), key=lambda x: h(args.seed, x["path"], x["line_start"]))
                x = cands[0]
                ev = [{"kind": "text", "path": x["path"], "line_start": x["line_start"], "line_end": x["line_end"],
                       "text_sha256": x["text_sha256"], "paraphrase": ""}]
            chosen.append({"stratum": "H_authority_target_silent" if silent_turn else "G_authority_target_has_text",
                           "family": topic, "cross_ecosystem": False, "relation_proxy": "n/a",
                           "source": {k: s[k] for k in ("repo", "path", "line_start", "line_end", "text_sha256", "signal_class")},
                           "target_repo": tgt, "target_evidence": ev})
            placed += 1

    for i, c in enumerate(chosen):
        c["shortlist_id"] = "s%03d" % i
        c["source"]["sha"] = pins[c["source"]["repo"]]["sha"]
        c["target_sha"] = pins[c["target_repo"]]["sha"]
    with open(args.out, "w", encoding="utf-8") as fh:
        for c in chosen:
            fh.write(json.dumps(c, sort_keys=True) + "\n")
    print(json.dumps({"shortlist": len(chosen), "by_stratum": Counter(c["stratum"] for c in chosen),
                      "by_family": Counter(c["family"] for c in chosen)}, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
