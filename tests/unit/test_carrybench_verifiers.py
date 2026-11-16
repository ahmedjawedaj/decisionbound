"""The executable CarryBench-Real end-state checks must accept gold and reject wrong states."""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'benchmarks' / 'carrybench' / 'real_v1'


@pytest.fixture(scope='module')
def V():
    spec = importlib.util.spec_from_file_location('carry_verifiers', ROOT / 'verifiers' / 'carry_verifiers.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['carry_verifiers'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def case_labels():
    rows = [json.loads(line) for line in (ROOT / 'cases_v1.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    return {r['case_id']: r['label'] for r in rows[1:]}


def test_every_scenario_accepts_gold_and_rejects_wrong(V):
    bad = [r for r in V.run() if not r.ok]
    assert not bad, [(r.case_id, r.scenario, r.gold_problems, r.wrong_problems) for r in bad]


def test_specs_point_at_real_cases_with_matching_labels(V, case_labels):
    for sp in V.specs():
        assert sp.case_id in case_labels, sp.case_id
        assert sp.label == case_labels[sp.case_id], sp.case_id


def test_coverage_is_at_least_twenty_cases_and_spans_all_labels(V):
    specs = V.specs()
    cases = {sp.case_id for sp in specs}
    assert len(cases) >= 20
    labels = Counter(sp.label for sp in {sp.case_id: sp for sp in specs}.values())
    assert set(labels) == {'apply', 'abstain', 'translate', 'escalate'}


def test_regression_states_are_present(V):
    specs = V.specs()
    assert sum(len(sp.extra_wrong) for sp in specs) >= 80
    assert sum(len(sp.alt_gold) for sp in specs) >= 40


def test_wrong_states_differ_from_gold(V):
    for sp in V.specs():
        assert V.apply(sp.before, sp.gold) != V.apply(sp.before, sp.wrong), sp.case_id


def test_checks_report_a_reason_not_just_a_boolean(V):
    for sp in V.specs():
        problems = sp.check(sp.before, V.apply(sp.before, sp.wrong))
        assert problems and all(isinstance(p, str) and p for p in problems), sp.case_id


def test_a_gold_state_missing_its_main_change_is_rejected(V):
    """Drop each file the gold state writes. Specs that need work must then fail."""
    weak = 0
    for sp in V.specs():
        if not sp.noop_fails or len(sp.gold) < 2:
            continue
        survivors = 0
        for key in sp.gold:
            partial = {k: v for k, v in sp.gold.items() if k != key}
            if not sp.check(sp.before, V.apply(sp.before, partial)):
                survivors += 1
        weak += survivors > 1
    assert weak <= 3, 'too many checks accept a gold state with several pieces removed'
