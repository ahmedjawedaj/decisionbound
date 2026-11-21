"""Tests for the human study analysis script. Offline and fast. Uses synthetic human files only."""
import hashlib
import importlib.util
import io
import json
import os
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
    shown = sorted(r.split("|")[1].strip() for r in rows)
    expected = sorted(name for p in study.DEFAULT_RESULTS for r in json.loads(p.read_text())["results"]
                      for name in [r.get("baseline") or r.get("method")])
    assert shown == expected
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


def test_same_annotator_id_is_refused(tmp_path, capsys):
    h1 = write_human(tmp_path / "h1.jsonl", {}, "H1")
    h2 = Path(write_human(tmp_path / "h2.jsonl", {FIRST: other_label(AI[FIRST])}, "H1"))
    h2.write_text(h2.read_text().replace("H1 reads", "Someone else reads"))
    with pytest.raises(SystemExit):
        study.main(["--human", h1, "--human", str(h2)])
    assert "same annotator_id" in capsys.readouterr().err


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
    bad = [("x.json", ["always"], [{"case_id": "cbh-999", "label": "abstain", "always": "abstain"}])]
    with pytest.raises(SystemExit) as exc:
        study.rescore(bad, {"cbh-999": "abstain"}, SCORED, 10, 1)
    assert "not in the scored file" in str(exc.value)


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


# ---- a small hand-computed fixture ----------------------------------------------------------------
TINY_IDS = ["t1", "t2", "t3", "t4", "t5", "t6"]
TINY_REPO = {"t1": "R1", "t2": "R1", "t3": "R1", "t4": "R2", "t5": "R2", "t6": "R2"}
TINY_AI = {"t1": "apply", "t2": "abstain", "t3": "abstain", "t4": "escalate", "t5": "translate", "t6": "abstain"}
TINY_HUMAN = {"t1": "apply", "t2": "abstain", "t3": "apply", "t4": "escalate", "t5": "translate", "t6": "escalate"}
TINY_SYS_A = {"t1": "apply", "t2": "abstain", "t3": "apply", "t4": "escalate", "t5": "abstain", "t6": "escalate"}


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def tiny_human(path, who, labels):
    rows = [{"annotator_id": who, "saw_ai_labels": False, "background": "engineer", "guide": "v1.1"}]
    for cid in TINY_IDS:
        label = labels[cid]
        rows.append({
            "case_id": cid, "label": label, "allowed": None, "confidence": 0.7, "strength": "medium",
            "deciding_tier": "T2", "rationale": f"{who} on {cid}", "translated_rule": "rule" if label == "translate" else None,
            "invalid_reason": None, "read_beyond_pointers": False, "notes": None})
    return write_jsonl(path, rows)


def tiny_setup(tmp_path, h2=None, repos=None):
    """Return the CLI arguments for a six case study with two repositories and two systems."""
    repo_of = repos or TINY_REPO
    scored = [{"format": "x"}] + [{"case_id": c, "label": TINY_AI[c], "source": {"repo": repo_of[c]}} for c in TINY_IDS]
    results = {
        "results": [{"baseline": "sysA"}, {"method": "sysB"}],
        "per_case": [{"case_id": c, "label": TINY_AI[c], "sysA": TINY_SYS_A[c], "sysB": "abstain"} for c in TINY_IDS],
    }
    res = tmp_path / "res.json"
    res.write_text(json.dumps(results))
    draft = tiny_human(tmp_path / "draft.jsonl", "draft", {c: "abstain" for c in TINY_IDS})
    h1 = tiny_human(tmp_path / "h1.jsonl", "H1", TINY_HUMAN)
    h2p = tiny_human(tmp_path / "h2.jsonl", "H2", h2 or TINY_HUMAN)
    return ["--human", h1, "--human", h2p, "--ai-draft", draft, "--results", str(res),
            "--scored", write_jsonl(tmp_path / "scored.jsonl", scored),
            "--cases", write_jsonl(tmp_path / "cases.jsonl", [{"case_id": c} for c in TINY_IDS]),
            "--excluded", write_jsonl(tmp_path / "excluded.jsonl", [{"note": "none"}])]


def tiny_run(capsys, tmp_path, h2=None, reps="200"):
    assert study.main(["--reps", reps, *tiny_setup(tmp_path, h2)]) == 0
    return capsys.readouterr().out


def test_exact_scores_on_the_hand_computed_fixture(tmp_path, capsys):
    part5 = section(tiny_run(capsys, tmp_path), "## Part 5")
    rows = {r.split("|")[1].strip(): [c.strip() for c in r.split("|")[2:-1]]
            for r in part5.splitlines() if r.startswith(("| sysA |", "| sysB |"))}
    assert sorted(rows) == ["sysA", "sysB"]
    # accuracy vs AI, accuracy vs human, difference, macro F1 vs human
    assert rows["sysA"][:4] == ["0.500", "0.833", "+0.333", "0.667"]
    assert rows["sysB"][:4] == ["0.500", "0.167", "-0.333", "0.071"]
    assert "6 cases. Label counts against the human reference: abstain 1, apply 2, escalate 2, translate 1." in part5


def test_interval_comes_from_resampling_whole_repositories(tmp_path, capsys):
    """With two repositories only four resamples exist, so each bound must be one of their accuracies."""
    part5 = section(tiny_run(capsys, tmp_path), "## Part 5")
    allowed = {"sysA": {0.667, 0.833, 1.0}, "sysB": {0.0, 0.167, 0.333}}
    for line in part5.splitlines():
        if line.startswith(("| sysA |", "| sysB |")):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            lo, hi = (float(v) for v in cells[5].split(" to "))
            assert lo <= hi
            assert lo in allowed[cells[0]] and hi in allowed[cells[0]], line
    again = section(tiny_run(capsys, tmp_path), "## Part 5")
    assert again == part5


def test_part_two_scores_each_human_separately(tmp_path, capsys):
    h2 = dict(TINY_HUMAN, t5="abstain")
    part2 = section(tiny_run(capsys, tmp_path, h2=h2), "## Part 2", "## Part 4")
    assert "H1 against the draft on 6 cases: four-way 1/6 (17%)" in part2
    assert "H2 against the draft on 6 cases: four-way 2/6 (33%)" in part2


def test_out_is_refused_when_it_would_overwrite_an_input(tmp_path):
    args = tiny_setup(tmp_path)
    for flag_value in args[1::2]:
        before = Path(flag_value).read_bytes()
        with pytest.raises(SystemExit) as exc:
            study.main([*args, "--out", flag_value, "--force"])
        assert exc.value.code == 2
        assert Path(flag_value).read_bytes() == before


def test_out_is_refused_inside_the_holdout_directory(tmp_path):
    target = HOLD / "must_not_be_written.md"
    target.unlink(missing_ok=True)
    try:
        with pytest.raises(SystemExit):
            study.main([*tiny_setup(tmp_path), "--out", str(target)])
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


def test_out_will_not_replace_an_existing_file_without_force(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    target = tmp_path / "report.md"
    target.write_text("keep me")
    with pytest.raises(SystemExit):
        study.main(["--reps", "20", *args, "--out", str(target)])
    assert target.read_text() == "keep me"
    assert study.main(["--reps", "20", *args, "--out", str(target), "--force"]) == 0
    assert target.read_text().startswith("# Human study analysis")


def test_report_file_is_written_as_utf8(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    h1 = Path(args[1])
    h1.write_text(h1.read_text().replace("H1 on t3", "H1 lit: \u0627\u0631\u062f\u0648 \u00e9"), encoding="utf-8")
    target = tmp_path / "r.md"
    assert study.main(["--reps", "20", *args, "--out", str(target)]) == 0
    capsys.readouterr()
    assert "\u0627\u0631\u062f\u0648" in target.read_text(encoding="utf-8")


def test_copy_with_one_changed_rationale_is_still_refused(tmp_path, capsys):
    h = write_human(tmp_path / "h.jsonl", {}, "H1")
    lines = Path(h).read_text().replace('"annotator_id": "H1"', '"annotator_id": "H2"').splitlines()
    lines[5] = lines[5].replace("H1 reads", "H1 sees")
    copy = tmp_path / "copy.jsonl"
    copy.write_text("\n".join(lines) + "\n")
    with pytest.raises(SystemExit):
        study.main(["--human", h, "--human", str(copy)])
    assert "looks like a copy" in capsys.readouterr().err


def test_two_humans_who_write_their_own_rationales_are_accepted(tmp_path, capsys):
    out = tiny_run(capsys, tmp_path, reps="20")
    assert "No disagreements between the two humans." in out


def test_missing_input_file_and_zero_reps_give_a_message(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        study.main(["--human", str(tmp_path / "nope.jsonl")])
    assert exc.value.code == 2 and "cannot read" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        study.main(["--reps", "0", *tiny_setup(tmp_path)])
    assert exc.value.code == 2


def test_result_file_that_does_not_line_up_is_refused(tmp_path):
    base = {"results": [{"baseline": "sysA"}],
            "per_case": [{"case_id": c, "label": TINY_AI[c], "sysA": "abstain"} for c in TINY_IDS]}
    scored = {c: {"label": TINY_AI[c], "source": {"repo": TINY_REPO[c]}} for c in TINY_IDS}
    ref = dict(TINY_HUMAN)

    def run_with(**changes):
        data = json.loads(json.dumps(base))
        for key, value in changes.items():
            data[key] = value(data[key])
        return study.rescore([("f.json", [r.get("baseline") or r.get("method") for r in data["results"]], data["per_case"])], ref, scored, 5, 1)

    assert run_with()
    for change, text in (
        (dict(per_case=lambda rows: rows + [rows[0]]), "more than once"),
        (dict(per_case=lambda rows: rows[:-2]), "no predictions for 2 scored case"),
        (dict(per_case=lambda rows: [{k: v for k, v in rows[0].items() if k != "sysA"}] + rows[1:]), "no prediction by"),
        (dict(results=lambda r: []), "names no systems"),
    ):
        with pytest.raises(SystemExit) as exc:
            run_with(**change)
        assert text in str(exc.value)


def test_extra_fields_in_a_result_row_are_not_systems(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    res = Path(args[args.index("--results") + 1])
    data = json.loads(res.read_text())
    for r in data["per_case"]:
        r["family"] = "x"
    res.write_text(json.dumps(data))
    part5 = section(study.main(["--reps", "20", *args]) == 0 and capsys.readouterr().out, "## Part 5")
    assert "| family |" not in part5 and "| label |" not in part5 and "| sysA |" in part5


def test_the_ai_column_uses_the_frozen_label_not_the_stored_row_label(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    res = Path(args[args.index("--results") + 1])
    data = json.loads(res.read_text())
    for r in data["per_case"]:
        r["label"] = "apply"
    res.write_text(json.dumps(data))
    part5 = section(study.main(["--reps", "20", *args]) == 0 and capsys.readouterr().out, "## Part 5")
    sys_a = next(l for l in part5.splitlines() if l.startswith("| sysA |")).split("|")
    assert sys_a[2].strip() == "0.500"


def test_annotator_id_with_a_pipe_does_not_break_the_table(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    h1 = Path(args[1])
    h1.write_text(h1.read_text().replace('"annotator_id": "H1"', '"annotator_id": "A|B"'))
    h2 = dict(TINY_HUMAN, t1="abstain")
    tiny_human(Path(args[3]), "H2", h2)
    out = section(study.main(["--reps", "20", *args]) == 0 and capsys.readouterr().out, "## Part 4", "## Part 5")
    header = next(l for l in out.splitlines() if l.startswith("| case |"))
    assert header.count("|") == next(l for l in out.splitlines() if l.startswith("| t1 |")).count("|")


def test_headings_do_not_repeat_the_comparison_title(tmp_path, capsys):
    out = tiny_run(capsys, tmp_path, reps="20")
    assert out.count("Generated by") == 1
    assert "## Parts 1 and 3" in out and "### H1 against the frozen AI labels" in out
    one = run(capsys, "--human", write_human(tmp_path / "solo.jsonl", {}, "H1"))
    assert "## Part 1: against the frozen AI pair" in one and "Parts 1 and 3" not in one


def test_macro_f1_note_is_in_the_report(tmp_path, capsys):
    assert "counts a label that is absent from the reference as 0" in tiny_run(capsys, tmp_path, reps="20")


def test_out_through_a_hard_link_is_refused(tmp_path):
    args = tiny_setup(tmp_path)
    link = tmp_path / "link.md"
    os.link(args[1], link)
    before = Path(args[1]).read_bytes()
    with pytest.raises(SystemExit):
        study.main([*args, "--out", str(link), "--force"])
    assert Path(args[1]).read_bytes() == before


def test_out_that_is_a_directory_or_has_no_folder_gives_a_message(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    for bad, text in ((str(tmp_path), "directory"), (str(tmp_path / "nope" / "r.md"), "does not exist")):
        with pytest.raises(SystemExit):
            study.main([*args, "--out", bad])
        assert text in capsys.readouterr().err


def test_input_that_is_not_json_gives_a_message(tmp_path, capsys):
    args = tiny_setup(tmp_path)
    Path(args[1]).write_text("not json\n")
    with pytest.raises(SystemExit) as exc:
        study.main(args)
    assert exc.value.code == 2 and "cannot read an input file" in capsys.readouterr().err


def two_humans_sharing(tmp_path, n_shared, suffix=""):
    """Second human copies the first human's rationale on the first n cases, and writes their own on the rest."""
    h1 = Path(write_human(tmp_path / "h1.jsonl", {}, "H1"))
    lines = h1.read_text().replace('"annotator_id": "H1"', '"annotator_id": "H2"').splitlines()
    for i in range(1 + n_shared, len(lines)):
        lines[i] = lines[i].replace("H1 reads", "H2 sees")
    if suffix:
        for i in range(1, 1 + n_shared):
            row = json.loads(lines[i])
            row["rationale"] = row["rationale"].upper() + suffix
            lines[i] = json.dumps(row)
    h2 = tmp_path / "h2.jsonl"
    h2.write_text("\n".join(lines) + "\n")
    return str(h1), str(h2)


def test_copy_guard_threshold_is_exactly_half(tmp_path, capsys):
    h1, h2 = two_humans_sharing(tmp_path, 20)
    with pytest.raises(SystemExit):
        study.main(["--human", h1, "--human", h2])
    assert "20 of 40" in capsys.readouterr().err
    h1, h2 = two_humans_sharing(tmp_path, 19)
    assert study.main(["--reps", "20", "--human", h1, "--human", h2]) == 0


def test_copy_guard_ignores_case_and_punctuation(tmp_path, capsys):
    h1, h2 = two_humans_sharing(tmp_path, 40, suffix=" .")
    with pytest.raises(SystemExit):
        study.main(["--human", h1, "--human", h2])
    assert "looks like a copy" in capsys.readouterr().err


def test_copy_guard_can_be_overridden(tmp_path, capsys):
    h1, h2 = two_humans_sharing(tmp_path, 25)
    assert study.main(["--reps", "20", "--human", h1, "--human", h2, "--allow-similar-rationales"]) == 0


def test_report_is_written_even_when_the_console_cannot_show_it(tmp_path, monkeypatch):
    args = tiny_setup(tmp_path)
    h1 = Path(args[1])
    h1.write_text(h1.read_text().replace("H1 on t3", "H1 lit: \u0627\u0631\u062f\u0648"), encoding="utf-8")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True))
    target = tmp_path / "r.md"
    assert study.main(["--reps", "20", *args, "--out", str(target)]) == 0
    assert "\u0627\u0631\u062f\u0648" in target.read_text(encoding="utf-8")
    assert b"# Human study analysis" in raw.getvalue()


def mirror_interval(repos, gold, preds, reps, seed):
    """An independent copy of the documented method: resample whole repositories, seed fixed."""
    import random
    rng = random.Random(seed)
    keys = sorted(set(repos))
    vals = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        idx = [i for k in picked for i, r in enumerate(repos) if r == k]
        vals.append(sum(gold[i] == preds[i] for i in idx) / len(idx))
    vals.sort()
    return round(vals[int(0.025 * reps)], 3), round(vals[int(0.975 * reps) - 1], 3)


SIX_REPOS = {c: f"r{i}" for i, c in enumerate(TINY_IDS, 1)}


@pytest.mark.parametrize("reps,seed", [(40, 17), (40, 5), (40, 99), (15, 17), (15, 5), (200, 17)])
def test_interval_matches_an_independent_resampler(tmp_path, capsys, reps, seed):
    """Six repositories give enough distinct resamples for the seed and the resample count to matter."""
    args = tiny_setup(tmp_path, repos=SIX_REPOS)
    assert study.main(["--reps", str(reps), "--seed", str(seed), *args]) == 0
    part5 = section(capsys.readouterr().out, "## Part 5")
    repos = [SIX_REPOS[c] for c in TINY_IDS]
    gold = [TINY_HUMAN[c] for c in TINY_IDS]
    for name, preds in (("sysA", [TINY_SYS_A[c] for c in TINY_IDS]), ("sysB", ["abstain"] * 6)):
        lo, hi = mirror_interval(repos, gold, preds, reps, seed)
        row = next(l for l in part5.splitlines() if l.startswith(f"| {name} |"))
        assert row.endswith(f"| {lo:.3f} to {hi:.3f} |"), (row, lo, hi)


def test_the_fixture_distinguishes_seeds_and_resample_counts():
    """Guards the test above. If these intervals were all equal the test could not catch a wrong seed."""
    repos = [SIX_REPOS[c] for c in TINY_IDS]
    gold = [TINY_HUMAN[c] for c in TINY_IDS]
    preds = [TINY_SYS_A[c] for c in TINY_IDS]
    found = {mirror_interval(repos, gold, preds, r, s) for r, s in [(40, 17), (40, 5), (15, 17), (15, 5), (2000, 17)]}
    assert len(found) >= 2
