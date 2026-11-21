"""Tests for the human study analysis script. Offline and fast. Uses synthetic human files only."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HOLD = Path(__file__).resolve().parents[2] / "benchmarks" / "carrybench" / "real_v2_holdout"
HA = HOLD / "human_annotation"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


study = load_module("analyse_human_study", HA / "tools" / "analyse_human_study.py")

IDS = [r["case_id"] for r in map(json.loads, open(HOLD / "cases_v2_holdout_candidates.jsonl")) if "case_id" in r]
SCORED = {r["case_id"]: r for r in map(json.loads, open(HOLD / "cases_v2_holdout_v1.jsonl")) if "case_id" in r}
AI = {c: r["label"] for c, r in SCORED.items()}
EXCLUDED = [c for c in IDS if c not in SCORED]
FIRST = sorted(SCORED)[0]


def write_human(path, labels, who, default="abstain", saw_ai=False):
    """Write a valid synthetic human file. Cases not in `labels` get the frozen AI label or `default`."""
    rows = [{"annotator_id": who, "saw_ai_labels": saw_ai, "background": "engineer", "guide": "v1.1"}]
    for cid in IDS:
        label = labels.get(cid, AI.get(cid, default))
        rows.append({
            "case_id": cid, "label": label, "allowed": None, "confidence": 0.7, "strength": "medium",
            "deciding_tier": None if label in ("invalid", "no_signal") else "T2",
            "rationale": f"{who} reads the pinned text as not deciding the question.",
            "translated_rule": "a rule" if label == "translate" else None,
            "invalid_reason": "bad pointer" if label == "invalid" else None,
            "read_beyond_pointers": False, "notes": None,
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def other_label(label):
    return "escalate" if label != "escalate" else "abstain"


def run(capsys, *args):
    assert study.main(["--reps", "50", *args]) == 0
    return capsys.readouterr().out


def section(text, start, end=None):
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end else body


def table_row(text, system):
    return next(line for line in text.splitlines() if line.startswith(f"| {system} |"))


def test_two_humans_who_match_the_ai_pair_change_no_system_score(tmp_path, capsys):
    h1 = write_human(tmp_path / "h1.jsonl", {}, "H1")
    h2 = write_human(tmp_path / "h2.jsonl", {}, "H2")
    out = run(capsys, "--human", h1, "--human", h2)
    part5 = section(out, "## Part 5")
    assert "It holds 40 of 40 cases." in part5
    assert "34 cases" not in part5 and "35 cases" in part5
    rows = [line for line in part5.splitlines() if line.startswith("| ") and "accuracy" not in line and "---" not in line]
    assert len(rows) >= 14
    assert all(r.split("|")[4].strip() == "+0.000" for r in rows)


def test_score_uses_the_human_label_not_the_ai_label(tmp_path, capsys):
    """If every human says abstain, the always-abstain system must score 1.000 against the humans."""
    all_abstain = {c: "abstain" for c in IDS}
    h = write_human(tmp_path / "h.jsonl", all_abstain, "H1")
    out = run(capsys, "--human", h)
    part5 = section(out, "## Part 5")
    row = table_row(part5, "majority(abstain)").split("|")
    assert row[3].strip() == "1.000"
    assert float(row[2]) < 0.5
    assert row[4].strip().startswith("+0.6")


def test_disputed_case_is_left_out_and_named(tmp_path, capsys):
    h1 = write_human(tmp_path / "h1.jsonl", {}, "H1")
    h2 = write_human(tmp_path / "h2.jsonl", {FIRST: other_label(AI[FIRST])}, "H2")
    out = run(capsys, "--human", h1, "--human", h2)
    part5 = section(out, "## Part 5")
    assert "It holds 39 of 40 cases." in part5
    assert f"1 case(s) are left out because the humans disagree and no adjudication has been done: {FIRST}." in part5
    assert "34 cases" in part5
    assert f"| {FIRST} | {AI[FIRST]} | {other_label(AI[FIRST])} |" in section(out, "## Part 4", "## Part 5")


def test_special_labels_never_enter_the_reference(tmp_path, capsys):
    h1 = write_human(tmp_path / "h1.jsonl", {FIRST: "invalid"}, "H1")
    h2 = write_human(tmp_path / "h2.jsonl", {FIRST: "invalid"}, "H2")
    out = run(capsys, "--human", h1, "--human", h2)
    part5 = section(out, "## Part 5")
    assert "It holds 39 of 40 cases." in part5
    assert f"1 case(s) are left out because a human marked them invalid or no_signal: {FIRST}." in part5
    assert "the humans disagree" not in part5


def test_one_human_is_named_as_such(tmp_path, capsys):
    out = run(capsys, "--human", write_human(tmp_path / "h.jsonl", {}, "H1"))
    assert "The reference is the labels of the single human." in out
    assert "H1 against H2" not in out
    assert "It holds 40 of 40 cases." in section(out, "## Part 5")


def test_cases_without_a_stored_prediction_are_named(tmp_path, capsys):
    out = run(capsys, "--human", write_human(tmp_path / "h.jsonl", {}, "H1"))
    part5 = section(out, "## Part 5")
    assert f"{len(EXCLUDED)} reference case(s) have no stored system prediction because the AI pair did not agree on them: " + ", ".join(EXCLUDED) + "." in part5


def test_part_four_lists_only_cases_where_a_rater_differs(tmp_path, capsys):
    h1 = write_human(tmp_path / "h1.jsonl", {}, "H1")
    h2 = write_human(tmp_path / "h2.jsonl", {FIRST: other_label(AI[FIRST])}, "H2")
    part4 = section(run(capsys, "--human", h1, "--human", h2), "## Part 4", "## Part 5")
    listed = {line.split("|")[1].strip() for line in part4.splitlines() if line.startswith("| cbh-")}
    assert FIRST in listed
    draft = study.cmp.load_human(study.DEFAULT_AI_DRAFT, IDS)[1]
    expected = set()
    for cid in IDS:
        prov = {}
        if cid not in SCORED:
            prov = next(json.loads(l) for l in open(HOLD / "excluded_v2_holdout.jsonl") if cid in l and "label_provenance" in l)["label_provenance"]["labels"]
        a = AI.get(cid, prov.get("A_v1_1_blind"))
        b = AI.get(cid, prov.get("B_v1_1_blind"))
        h2l = other_label(AI[FIRST]) if cid == FIRST else AI.get(cid, "abstain")
        if len({a, b, AI.get(cid, "abstain"), h2l, draft[cid]["label"]}) > 1:
            expected.add(cid)
    assert listed == expected


def test_draft_comparison_is_computed_from_the_draft(tmp_path, capsys):
    h = write_human(tmp_path / "h.jsonl", {}, "H1")
    out = run(capsys, "--human", h)
    draft = study.cmp.load_human(study.DEFAULT_AI_DRAFT, IDS)[1]
    human = study.cmp.load_human(Path(h), IDS)[1]
    pairs = [(draft[c]["label"], human[c]["label"]) for c in IDS]
    expected = f"H1 against the draft on 40 cases: " + study.cmp.fmt(study.cmp.summarise(pairs))
    assert expected in section(out, "## Part 2", "## Part 4")


def test_warns_when_a_human_saw_the_ai_labels(tmp_path, capsys):
    out = run(capsys, "--human", write_human(tmp_path / "h.jsonl", {}, "H1", saw_ai=True))
    assert "Warning: H1 declared `saw_ai_labels` = True." in out
    assert "Warning" not in run(capsys, "--human", write_human(tmp_path / "ok.jsonl", {}, "H1"))


def test_a_copy_with_a_new_annotator_id_is_refused(tmp_path, capsys):
    h = write_human(tmp_path / "h.jsonl", {}, "H1")
    copy = tmp_path / "copy.jsonl"
    copy.write_text(Path(h).read_text().replace('"annotator_id": "H1"', '"annotator_id": "H2"'))
    with pytest.raises(SystemExit) as exc:
        study.main(["--human", h, "--human", str(copy)])
    assert exc.value.code == 2
    assert "looks like a copy" in capsys.readouterr().err


def test_same_annotator_id_is_refused(tmp_path):
    h1 = write_human(tmp_path / "h1.jsonl", {}, "H1")
    h2 = write_human(tmp_path / "h2.jsonl", {FIRST: other_label(AI[FIRST])}, "H1")
    with pytest.raises(SystemExit):
        study.main(["--human", h1, "--human", h2])


def test_three_humans_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        study.main(["--human", "a", "--human", "b", "--human", "c"])


def test_incomplete_human_file_is_refused(tmp_path):
    h = Path(write_human(tmp_path / "h.jsonl", {}, "H1"))
    lines = h.read_text().splitlines()
    h.write_text("\n".join(lines[:-2]) + "\n")
    with pytest.raises(SystemExit) as exc:
        study.main(["--human", str(h)])
    assert "missing cases" in str(exc.value)


def test_prediction_for_unknown_case_is_refused():
    bad = [("x.json", [{"case_id": "cbh-999", "label": "abstain", "always": "abstain"}])]
    with pytest.raises(SystemExit):
        study.rescore(bad, {"cbh-999": "abstain"}, SCORED, 10, 1)


def test_out_file_matches_printed_report_and_inputs_are_untouched(tmp_path, capsys):
    watched = [HOLD / "annotations" / "labels_A_v1_1.jsonl", HOLD / "annotations" / "labels_B_v1_1.jsonl",
               HOLD / "cases_v2_holdout_v1.jsonl", HOLD / "excluded_v2_holdout.jsonl", study.DEFAULT_AI_DRAFT,
               *study.DEFAULT_RESULTS]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    target = tmp_path / "report.md"
    out = run(capsys, "--human", write_human(tmp_path / "h.jsonl", {}, "H1"), "--out", str(target))
    assert target.read_text() == out
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}


def test_reference_labels_unit():
    def human(**labels):
        return ({"annotator_id": "x"}, {c: {"label": v} for c, v in labels.items()})
    ref, how, disputed, special = study.reference_labels([
        human(a="apply", b="apply", c="abstain", d="invalid", e="apply"),
        human(a="apply", b="escalate", c="abstain", d="invalid", e="no_signal"),
    ])
    assert ref == {"a": "apply", "c": "abstain"}
    assert disputed == ["b"] and special == ["d", "e"]
    assert "both humans" in how
    one, how1, d1, s1 = study.reference_labels([human(a="apply", b="no_signal")])
    assert one == {"a": "apply"} and s1 == ["b"] and d1 == [] and "single human" in how1
