"""The extra AI label draft is filed apart from the frozen and human labels, and its numbers are real."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "carrybench" / "real_v2_holdout"
FOLDER = ROOT / "annotations" / "ai_other_family"
DRAFT = FOLDER / "labels_gpt6_ai_draft_v1_1.jsonl"
TOOLS = ROOT / "human_annotation" / "tools"
sys.path.insert(0, str(TOOLS))
import check_human_labels as checker  # noqa: E402
import compare_labels as cmp  # noqa: E402
import make_blind_packet as packet  # noqa: E402


def _rows(path: Path) -> dict[str, dict]:
    return {r["case_id"]: r for r in map(json.loads, open(path, encoding="utf-8")) if "case_id" in r}


def _header(path: Path) -> dict:
    return json.loads(open(path, encoding="utf-8").readline())


def test_file_hash_is_pinned_in_the_readme():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    pinned = re.search(r"\| SHA-256 \| `([0-9a-f]{64})` \|", readme)
    assert pinned, "README must record the SHA-256"
    assert hashlib.sha256(DRAFT.read_bytes()).hexdigest() == pinned.group(1)


def test_format_is_valid_but_every_marker_says_not_human():
    ids = checker._case_ids(ROOT / "cases_v2_holdout_candidates.jsonl")
    assert checker.check(checker.read_rows(DRAFT), ids) == []
    header = _header(DRAFT)
    assert header["annotator_id"] == "gpt6_ai_draft"
    assert "not human" in header["background"] and header["guide"] == "v1.1"
    assert header["saw_ai_labels"] is False  # the README reports this self statement
    rows = _rows(DRAFT)
    assert len(rows) == 40
    for row in rows.values():
        assert "Not a human annotation" in row["notes"]
        assert row["read_beyond_pointers"] is False


def test_frozen_labels_are_untouched_by_the_draft_and_it_is_not_in_the_human_packet(tmp_path):
    out = tmp_path / "p"
    names = packet.build(out)
    assert not any("ai_other_family" in n or "gpt6" in n for n in names)
    assert not any(n.endswith("README.md") and "AI draft labels" in (out / n).read_text(encoding="utf-8") for n in names)
    text = "".join((out / n).read_bytes().decode("utf-8", "replace") for n in names)
    for row in _rows(DRAFT).values():
        assert row["rationale"] not in text
    scored = cmp.load_cases(ROOT / "cases_v2_holdout_v1.jsonl")
    assert not any("gpt6" in json.dumps(r) for r in scored.values())
    for name in ("labels_A_v1_1.jsonl", "labels_B_v1_1.jsonl"):
        assert "gpt6" not in (ROOT / "annotations" / name).read_text(encoding="utf-8")


def test_readme_numbers_are_recomputed_from_the_files():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    draft = _rows(DRAFT)
    for name, agree, kappa in (("A", 30, "0.66"), ("B", 29, "0.63")):
        frozen = _rows(ROOT / "annotations" / f"labels_{name}_v1_1.jsonl")
        s = cmp.summarise([(frozen[c]["label"], draft[c]["label"]) for c in sorted(frozen)])
        assert (s["n"], s["agree_four_way"], f"{s['kappa_four_way']:.2f}") == (40, agree, kappa)
        assert f"| Annotator {name}, all 40 cases | 40 | {agree}/40 ({agree / 40 * 100:.1f}%) | {kappa} |" in readme
    scored = cmp.load_cases(ROOT / "cases_v2_holdout_v1.jsonl")
    s = cmp.summarise([(scored[c]["label"], draft[c]["label"]) for c in sorted(scored)])
    assert (s["n"], s["agree_four_way"], f"{s['kappa_four_way']:.2f}") == (35, 27, "0.69")
    assert (s["n_two_way"], s["agree_two_way"], f"{s['kappa_two_way']:.2f}") == (33, 28, "0.69")
    assert "| Frozen scored set, four labels | 35 | 27/35 (77%) | 0.69 |" in readme
    assert "| Frozen scored set, carry versus stop | 33 | 28/33 (85%) | 0.69 |" in readme


def test_disagreement_table_matches_the_files():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    scored = cmp.load_cases(ROOT / "cases_v2_holdout_v1.jsonl")
    draft = _rows(DRAFT)
    actual = {c: (scored[c]["label"], draft[c]["label"]) for c in scored if scored[c]["label"] != draft[c]["label"]}
    table = {m[0]: (m[1], m[2]) for m in re.findall(r"^\| (cbh-\d{3}) \| (\w+) \| (\w+) \|", readme, re.M)}
    assert table == actual and len(actual) == 8
    # the README says three of the eight sit under ambiguity 4 or 5 of the frozen report
    report = (ROOT / "AGREEMENT_REPORT.md").read_text(encoding="utf-8")
    listed: dict[int, set[str]] = {}
    for line in report.splitlines():
        m = re.match(r"^([1-5])\. .*?\(cbh-(\d{3})((?:, \d{3})*)\)", line)
        if m:
            listed[int(m.group(1))] = {"cbh-" + n for n in [m.group(2), *re.findall(r"\d{3}", m.group(3))]}
    claimed = {4: {"cbh-003", "cbh-011"}, 5: {"cbh-020"}}
    for number, cases in claimed.items():
        assert cases <= listed[number] and cases <= set(actual), number
    assert {"cbh-001", "cbh-021"} <= listed[1] and {"cbh-001", "cbh-021"} <= set(actual)
    assert "Three of the eight (cbh-003, 011 and 020)" in readme
    assert "which lists no cases" in readme and not any(c in listed.get(2, set()) for c in actual)


def test_label_counts_in_readme():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    from collections import Counter
    draft = _rows(DRAFT)
    scored = cmp.load_cases(ROOT / "cases_v2_holdout_v1.jsonl")

    def fmt(c: Counter) -> str:
        return ", ".join(f"{k} {c[k]}" for k in ("abstain", "apply", "escalate", "translate", "invalid") if c[k])

    assert f"Draft on all 40 cases: {fmt(Counter(r['label'] for r in draft.values()))}." in readme
    assert f"Draft on the 35 scored cases: {fmt(Counter(draft[c]['label'] for c in scored))}." in readme
    assert f"Frozen scored labels: {fmt(Counter(r['label'] for r in scored.values()))}." in readme


def test_other_documents_and_prose_claims_agree_with_the_files():
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    report = (ROOT / "AGREEMENT_REPORT.md").read_text(encoding="utf-8")
    changelog = (ROOT.parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "agrees with the frozen scored labels on 27 of 35 cases (kappa 0.69)" in report
    assert "It is not a human label set." in report and "annotations/ai_other_family/README.md" in report
    entry = next(line for line in changelog.splitlines() if "extra AI label draft" in line)
    for phrase in ("27 of 35 cases (kappa 0.69, carry versus stop 28 of 33)", "annotator A on 30 of 40", "with B on 29 of 40",
                   "Three of its eight disagreements (cbh-003, 011, 020)", "must not be merged with human labels"):
        assert phrase in entry, phrase
    # frozen pair figures quoted in the README
    a, b = (_rows(ROOT / "annotations" / f"labels_{n}_v1_1.jsonl") for n in "AB")
    s = cmp.summarise([(a[c]["label"], b[c]["label"]) for c in sorted(a)])
    assert (s["agree_four_way"], f"{s['kappa_four_way']:.2f}") == (35, "0.83")
    assert "35 of 40 (0.875, kappa 0.83)" in readme
    # the draft's labels on the contested cases
    draft = _rows(DRAFT)
    for case, label in (("cbh-017", "apply"), ("cbh-025", "abstain"), ("cbh-028", "abstain"), ("cbh-031", "escalate"), ("cbh-034", "abstain")):
        assert draft[case]["label"] == label
    assert "apply (cbh-017), abstain (cbh-025, 028 and 034) and escalate (cbh-031)" in readme
    # the rules and the not-human framing
    for phrase in ("It is an AI draft. It is not a human label set", "Never merge it with the human labels", "must set `saw_ai_labels` to true",
                   "Passing the checker does not make it a human file"):
        assert phrase in readme, phrase
