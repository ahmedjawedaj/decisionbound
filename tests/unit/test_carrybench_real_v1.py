"""Integrity checks for the CarryBench-Real v1 dataset package.

These run offline against the committed files. Re-hashing provenance pointers
needs the pinned clones: set CARRYBENCH_CLONES to a directory built by
benchmarks/carrybench/real_v1/tools/fetch_pins.py to enable that test.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'benchmarks' / 'carrybench' / 'real_v1'
LABELS = {'apply', 'translate', 'abstain', 'escalate'}
SPDX = {'MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'MPL-2.0', 'MIT OR Apache-2.0'}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


@pytest.fixture(scope='module')
def data():
    rows = read_jsonl(ROOT / 'cases_v1.jsonl')
    return rows[0], rows[1:], read_jsonl(ROOT / 'excluded_v1.jsonl')


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_header_is_honest_and_counts_match(data):
    header, cases, excluded = data
    assert header['dataset_id'] == 'carrybench_real_v1'
    assert header['synthetic'] is False
    assert header['validated'] is False
    assert header['case_count'] == len(cases) == 66
    assert 'No human' in header['note']
    assert len(excluded) == 9


def test_cases_are_well_formed_and_unique(data):
    _, cases, excluded = data
    ids = [c['case_id'] for c in cases]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {c['case_id'] for c in excluded}
    for c in cases:
        assert c['format'] == 'carrybench-real-case-v1'
        assert c['label'] in LABELS
        assert c['label_status'] == 'agreed_two_blind_llm_annotators'
        assert c['source']['repo'] != c['target']['repo']
        assert c['target']['task'] and c['verifier_note']
    assert Counter(c['label'] for c in cases) == {'abstain': 23, 'apply': 19, 'translate': 13, 'escalate': 11}


def test_excluded_cases_carry_no_label(data):
    _, _, excluded = data
    for c in excluded:
        assert c['label'] is None
        assert c['label_status'] in {'contested_excluded', 'dropped_no_signal'}


def test_provenance_only_no_third_party_text(data):
    _, cases, excluded = data
    for c in cases + excluded:
        pointers = [c['source']] + c['target']['evidence']
        for p in pointers:
            assert len(p['paraphrase'].split()) <= 40, c['case_id']
            assert '```' not in p['paraphrase']
            if p.get('text_sha256'):
                assert re.fullmatch(r'[0-9a-f]{64}', p['text_sha256'])
        assert re.fullmatch(r'[0-9a-f]{40}', c['source']['sha'])
        assert re.fullmatch(r'[0-9a-f]{40}', c['target']['sha'])
        assert c['source']['spdx'] in SPDX and c['target']['spdx'] in SPDX
        assert c['source']['url'].startswith('https://github.com/')


def test_pins_cover_every_repository(data):
    _, cases, excluded = data
    pins = {r['repo']: r for r in csv.DictReader((ROOT / 'pins.csv').open(encoding='utf-8'))}
    for c in cases + excluded:
        for side in ('source', 'target'):
            pin = pins[c[side]['repo']]
            assert pin['sha'] == c[side]['sha'], (c['case_id'], side)
            assert pin['spdx'] in SPDX


def test_labels_match_the_recorded_adjudication(data):
    _, cases, excluded = data
    adjudicated = {}
    for name in ('adjudication_pilot', 'adjudication_new'):
        for row in read_jsonl(ROOT / 'annotations' / f'{name}.jsonl'):
            adjudicated[row['case_id']] = row
    for c in cases:
        row = adjudicated[c['case_id']]
        assert row['status'] in {'agreed_v1', 'agreed_v1_1'}
        assert row['adjudicated_label'] == c['label']
        votes = [v for k, v in row['labels'].items() if not k.startswith('A')]
        assert len(votes) == 2 and set(votes) == {c['label']}, c['case_id']
    for c in excluded:
        assert adjudicated[c['case_id']]['adjudicated_label'] is None


def test_pilot_first_labels_are_preserved(data):
    _, cases, _ = data
    pilot = [c for c in cases if c['label_provenance']['set'] == 'pilot']
    assert len(pilot) == 32
    assert all(c['label_provenance'].get('first_label') in LABELS for c in pilot)
    assert sum(c['label_provenance']['first_label'] != c['label'] for c in pilot) >= 1


def test_metric_helpers_are_correct():
    ev = load_tool('evaluate_baselines')
    gold = ['apply', 'translate', 'abstain', 'escalate']
    assert ev.macro_f1(gold, gold) == 1.0
    m = ev.metrics(gold, ['apply', 'abstain', 'apply', 'escalate'])
    assert m['accuracy'] == 0.5
    assert m['harmful_apply_rate'] == 0.5      # one of two withhold cases got apply
    assert m['missed_transfer_rate'] == 0.5    # translate was withheld
    assert m['transfer_vs_withhold'] == 0.5
    assert m['per_label_recall']['translate'] == 0.0


def test_bootstrap_is_deterministic_and_clustered():
    ev = load_tool('evaluate_baselines')
    cases = [{'label': 'apply', 'source': {'repo': 'a/x'}}, {'label': 'abstain', 'source': {'repo': 'a/x'}},
             {'label': 'apply', 'source': {'repo': 'b/y'}}, {'label': 'escalate', 'source': {'repo': 'c/z'}}]
    preds = {'p': ['apply', 'apply', 'abstain', 'escalate']}
    first = ev.cluster_bootstrap(cases, preds, 200, 3)
    assert first == ev.cluster_bootstrap(cases, preds, 200, 3)
    low, high = first['p']['accuracy']
    assert 0.0 <= low <= high <= 1.0


@pytest.mark.skipif(not os.environ.get('CARRYBENCH_CLONES'), reason='pinned clones not available')
def test_every_pointer_rehashes_against_pinned_clones():
    clones = os.environ['CARRYBENCH_CLONES']
    for name in ('cases_v1.jsonl', 'excluded_v1.jsonl'):
        proc = subprocess.run([sys.executable, '-I', str(ROOT / 'tools' / 'mine_decisions.py'), 'verify',
                               '--cases', str(ROOT / name), '--clones-dir', clones],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)['mismatches'] == 0
