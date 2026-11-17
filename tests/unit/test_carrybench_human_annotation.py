"""Tests for the human annotation packet, label checker and agreement report. Offline and fast."""
import importlib.util
import json
import subprocess
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


checker = load_module("check_human_labels", HA / "tools" / "check_human_labels.py")
packet = load_module("make_blind_packet", HA / "tools" / "make_blind_packet.py")
compare = load_module("compare_labels", HA / "tools" / "compare_labels.py")

IDS = [r["case_id"] for r in map(json.loads, open(HOLD / "cases_v2_holdout_candidates.jsonl")) if "case_id" in r]


def good_rows(label="abstain"):
    rows = [{"annotator_id": "H1", "saw_ai_labels": False, "background": "engineer", "guide": "v1.1"}]
    for cid in IDS:
        rows.append({
            "case_id": cid, "label": label, "allowed": None, "confidence": 0.7, "strength": "medium",
            "deciding_tier": "T2", "rationale": "The pinned text does not decide the question.",
            "translated_rule": None, "invalid_reason": None, "read_beyond_pointers": False, "notes": None,
        })
    return rows


def problems(rows):
    return checker.check(rows, IDS)


def test_forty_cases():
    assert len(IDS) == 40


def test_valid_file_passes():
    assert problems(good_rows()) == []


def test_missing_case_reported():
    rows = good_rows()
    del rows[5]
    assert any("missing" in p for p in problems(rows))


def test_duplicate_case_reported():
    rows = good_rows()
    rows.append(dict(rows[1]))
    assert any("more than once" in p for p in problems(rows))


def test_extra_case_reported():
    rows = good_rows()
    rows.append(dict(rows[1], case_id="cbh-999"))
    assert problems(rows)


def test_bad_label_rejected():
    rows = good_rows()
    rows[1]["label"] = "maybe"
    assert problems(rows)


@pytest.mark.parametrize("value", [-0.1, 1.5, True, "high"])
def test_confidence_must_be_number_in_range(value):
    rows = good_rows()
    rows[1]["confidence"] = value
    assert problems(rows)


def test_invalid_needs_reason_and_no_tier():
    rows = good_rows()
    rows[1].update(label="invalid", deciding_tier=None, invalid_reason=None)
    assert problems(rows)
    rows[1]["invalid_reason"] = "The pointer does not match the pinned text."
    assert problems(rows) == []


def test_translate_needs_rule():
    rows = good_rows()
    rows[1]["label"] = "translate"
    assert problems(rows)
    rows[1]["translated_rule"] = "Use the target repository's own runner."
    assert problems(rows) == []


def test_long_rationale_rejected():
    rows = good_rows()
    rows[1]["rationale"] = "x" * 401
    assert problems(rows)


def test_code_fence_in_rationale_rejected():
    rows = good_rows()
    rows[1]["rationale"] = "see ```code```"
    assert problems(rows)


def test_reading_beyond_pointers_needs_notes():
    rows = good_rows()
    rows[1]["read_beyond_pointers"] = True
    assert problems(rows)
    rows[1]["notes"] = "Opened the repository README to check scope."
    assert problems(rows) == []


def test_wrong_guide_version_rejected():
    rows = good_rows()
    rows[0]["guide"] = "v1.2"
    assert problems(rows)


def test_missing_header_rejected():
    assert problems(good_rows()[1:])


def test_blank_template_is_rejected(tmp_path):
    out = tmp_path / "p"
    packet.build(out)
    rows = [json.loads(line) for line in open(out / "labels_template.jsonl")]
    assert len(rows) == 41
    assert problems(rows)


def test_packet_contents_and_no_ai_labels(tmp_path):
    out = tmp_path / "p"
    names = packet.build(out)
    assert "MANIFEST.sha256" in names
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert files == set(names)
    assert not any("annotations" in f or "results" in f or "DO_NOT_SHOW" in f or "excluded" in f for f in files)
    assert (out / "cases_v2_holdout_candidates.jsonl").read_bytes() == (HOLD / "cases_v2_holdout_candidates.jsonl").read_bytes()
    text = "".join((out / f).read_text(encoding="utf-8") for f in files if f.endswith((".jsonl", ".md", ".csv")))
    for marker in ("label_provenance", "contested", "A_v1_1", "B_v1_1", "stratum", "shortlist"):
        assert marker not in text, marker
    for ai in (HOLD / "annotations" / "labels_A_v1_1.jsonl", HOLD / "annotations" / "labels_B_v1_1.jsonl"):
        for row in map(json.loads, open(ai)):
            for field in ("rationale", "notes", "translated_rule"):
                value = row.get(field)
                if isinstance(value, str) and len(value) > 40:
                    assert value not in text and json.dumps(value)[1:-1] not in text
    for note in ("notes_A.md", "notes_B.md"):
        for line in open(HOLD / "annotations" / note, encoding="utf-8"):
            if len(line.strip()) > 60:
                assert line.strip() not in text
    for row in map(json.loads, open(out / "cases_v2_holdout_candidates.jsonl")):
        if "case_id" in row:
            assert row["label_status"] == "unlabelled_candidate"
            assert not {"label", "label_provenance", "gold"} & set(row)


def test_packet_refuses_non_empty_dir(tmp_path):
    (tmp_path / "x").write_text("a")
    with pytest.raises(SystemExit):
        packet.build(tmp_path)


def test_packet_checker_runs_from_packet(tmp_path):
    out = tmp_path / "p"
    packet.build(out)
    (out / "mine.jsonl").write_text("\n".join(json.dumps(r) for r in good_rows()) + "\n")
    r = subprocess.run([sys.executable, "-I", "tools/check_human_labels.py", "mine.jsonl", "--cases",
                        "cases_v2_holdout_candidates.jsonl"], cwd=out, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_unknown_fields_rejected():
    rows = good_rows()
    rows[1]["extra"] = 1
    assert any("unknown field" in p for p in problems(rows))
    rows = good_rows()
    rows[0]["extra"] = 1
    assert any("unknown field" in p for p in problems(rows))


def test_allowed_must_be_null_for_special_labels():
    rows = good_rows()
    rows[1].update(label="no_signal", deciding_tier=None, allowed="abstain")
    assert any("allowed must be null" in p for p in problems(rows))


def test_invisible_only_rationale_rejected():
    rows = good_rows()
    rows[1]["rationale"] = "\u200b \u200b"
    assert problems(rows)


def test_non_object_lines_give_a_message_not_a_crash():
    assert problems([{"annotator_id": "H"}, [1, 2]])
    assert problems([5])


def test_bom_file_is_readable(tmp_path):
    f = tmp_path / "bom.jsonl"
    f.write_bytes(b"\xef\xbb\xbf" + ("\n".join(json.dumps(r) for r in good_rows()) + "\n").encode())
    assert problems(checker.read_rows(f)) == []


def test_kappa_values():
    assert compare.cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")]) == pytest.approx(1.0)
    assert compare.cohen_kappa([("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")]) == pytest.approx(-1.0)
    assert compare.cohen_kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]) == pytest.approx(0.0)
    assert compare.cohen_kappa([("a", "a"), ("a", "a")]) is None
    assert compare.cohen_kappa([]) is None


def test_two_way_grouping():
    assert compare.two_way("apply") == compare.two_way("translate") == "act"
    assert compare.two_way("abstain") == compare.two_way("escalate") == "hold"
    assert compare.two_way("invalid") is None and compare.two_way("no_signal") is None


def write_human(path, labels, who):
    rows = good_rows()
    rows[0]["annotator_id"] = who
    for r in rows[1:]:
        r["label"] = labels.get(r["case_id"], "abstain")
        if r["label"] == "invalid":
            r.update(deciding_tier=None, invalid_reason="bad pointer")
        if r["label"] == "translate":
            r["translated_rule"] = "rule"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_compare_reports_disagreements(tmp_path, capsys):
    scored = compare.load_cases(HOLD / "cases_v2_holdout_v1.jsonl")
    ai = {c: r["label"] for c, r in scored.items()}
    h1, h2 = tmp_path / "h1.jsonl", tmp_path / "h2.jsonl"
    write_human(h1, ai, "H1")
    flip = next(iter(ai))
    other = "escalate" if ai[flip] != "escalate" else "abstain"
    write_human(h2, dict(ai, **{flip: other}), "H2")
    assert compare.main(["--human", str(h1), "--human", str(h2)]) == 0
    out = capsys.readouterr().out
    assert "On the 35 of 35 scored cases" in out
    assert "On the 40 cases both labelled" in out
    assert f"| {flip} |" in out
    assert "H1 against the frozen AI labels" in out
    assert "H1 against H2" in out
    assert "35/35" in out


def test_compare_rejects_three_humans(tmp_path):
    with pytest.raises(SystemExit):
        compare.main(["--human", "a", "--human", "b", "--human", "c"])


def test_compare_refuses_incomplete_human_file(tmp_path):
    h = tmp_path / "h.jsonl"
    write_human(h, {}, "H1")
    lines = h.read_text().splitlines()
    h.write_text("\n".join(lines[:-3]) + "\n")
    with pytest.raises(SystemExit) as exc:
        compare.main(["--human", str(h)])
    assert "missing cases" in str(exc.value)


def test_two_way_leaves_out_special_labels(tmp_path, capsys):
    scored = compare.load_cases(HOLD / "cases_v2_holdout_v1.jsonl")
    ai = {c: r["label"] for c, r in scored.items()}
    h = tmp_path / "h.jsonl"
    victim = next(iter(ai))
    write_human(h, dict(ai, **{victim: "invalid"}), "H1")
    assert compare.main(["--human", str(h)]) == 0
    out = capsys.readouterr().out
    assert "1 pair(s) left out because a label was invalid or no_signal" in out
    assert "Two-way" in out and "/34" in out
