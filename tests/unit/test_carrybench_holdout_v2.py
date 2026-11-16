"""Integrity checks for the CarryBench-Real v2 holdout. Offline and fast."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "carrybench" / "real_v2_holdout"
LABELS = {"apply", "translate", "abstain", "escalate"}


def load(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def by_id(rows):
    return {r["case_id"]: r for r in rows if "case_id" in r}


@pytest.fixture(scope="module")
def data():
    return {
        "cand": by_id(load(ROOT / "cases_v2_holdout_candidates.jsonl")),
        "scored": by_id(load(ROOT / "cases_v2_holdout_v1.jsonl")),
        "excluded": by_id(load(ROOT / "excluded_v2_holdout.jsonl")),
        "a": by_id(load(ROOT / "annotations" / "labels_A_v1_1.jsonl")),
        "b": by_id(load(ROOT / "annotations" / "labels_B_v1_1.jsonl")),
    }


def test_candidates_stay_unlabelled(data):
    assert len(data["cand"]) == 40
    for row in data["cand"].values():
        assert row["label_status"] == "unlabelled_candidate"
        assert not {"label", "gold", "verifier_note", "label_provenance"} & set(row)


def test_every_candidate_is_scored_or_excluded_exactly_once(data):
    assert set(data["scored"]) | set(data["excluded"]) == set(data["cand"])
    assert not set(data["scored"]) & set(data["excluded"])
    assert (len(data["scored"]), len(data["excluded"])) == (35, 5)


def test_scored_labels_are_exactly_the_agreed_annotator_labels(data):
    for cid, row in data["scored"].items():
        la, lb = data["a"][cid]["label"], data["b"][cid]["label"]
        assert la == lb and la in LABELS and row["label"] == la
        assert row["label_status"] == "agreed_two_blind_llm_annotators"
    for cid, row in data["excluded"].items():
        la, lb = data["a"][cid]["label"], data["b"][cid]["label"]
        assert (la != lb or la not in LABELS) and row["label"] is None
        assert row["label_status"] == "contested_excluded"


def test_case_content_is_unchanged_by_labelling(data):
    for cid, row in {**data["scored"], **data["excluded"]}.items():
        cand = data["cand"][cid]
        assert row["source"] == cand["source"] and row["target"] == cand["target"]


def test_stored_results_match_the_frozen_labels(data):
    for name in ("baselines_v2_holdout.json", "decisionbound_v2_holdout.json"):
        result = json.load(open(ROOT / "results" / name, encoding="utf-8"))
        per_case = {r["case_id"]: r["label"] for r in result["per_case"]}
        assert per_case == {cid: r["label"] for cid, r in data["scored"].items()}


def test_no_third_party_text_fields_are_stored(data):
    for row in data["cand"].values():
        assert set(row["source"]) <= {"repo", "sha", "path", "line_start", "line_end", "url", "spdx",
                                       "text_sha256", "paraphrase", "scope_conditions"}
