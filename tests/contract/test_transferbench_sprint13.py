"""TransferBench guardrails: prevent leakage and misleading research metrics."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

import pytest

from decisionbound.transferbench import load_cases, evaluate, predict, METHODS

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT/'benchmarks/transferbench/fixtures/synthetic_smoke_v1.jsonl'


def test_synthetic_baselines_and_explicit_denominators():
    cases=load_cases(DATA)
    report=evaluate(cases, seed=2026, bootstrap_samples=200)
    assert report['case_count'] == 12 and report['family_count'] == 3
    assert report['kind'] == 'policy_oracle_smoke_not_empirical_agent_results'
    assert report['arms']['decisionbound']['oracle_action_acceptance'] == 1.0
    assert report['arms']['full-transfer']['harmful_policy_action_count'] > 0
    assert report['arms']['no-transfer']['oracle_action_acceptance'] < 1.0
    assert report['arms']['decisionbound']['harmful_per_transfer'] == 0
    assert report['verified_software_tasks'] is None
    assert report['model_cost_usd'] is None and not report['paper_conclusions_supported']


def test_oracle_data_not_consulted_by_policy():
    cases=load_cases(DATA)
    from dataclasses import replace
    base=cases[2]
    assert predict('decisionbound',base) == predict('decisionbound',replace(base,allowed_actions=frozenset({'escalate'}), label_reason='poisoned'))


def test_deterministic_group_bootstrap():
    cases=load_cases(DATA)
    assert evaluate(cases,seed=17,bootstrap_samples=400) == evaluate(cases,seed=17,bootstrap_samples=400)
    assert len(evaluate(cases,seed=17,bootstrap_samples=400)['arms']['decisionbound']['oracle_action_acceptance_95ci']) == 2
    with pytest.raises(ValueError):
        evaluate(cases,bootstrap_samples=2)


def test_repo_family_split_leakage_rejected(tmp_path):
    rows=[json.loads(x) for x in DATA.read_text().splitlines()]
    rows[0]['repo_family']=rows[2]['repo_family']
    path=tmp_path/'leak.jsonl'
    path.write_text('\n'.join(json.dumps(x) for x in rows))
    with pytest.raises(ValueError,match='leakage'):
        load_cases(path)


def test_duplicate_record_rejected(tmp_path):
    lines=DATA.read_text().splitlines()
    path=tmp_path/'duplicate.jsonl'
    path.write_text('\n'.join([*lines,lines[0]]))
    with pytest.raises(ValueError,match='line'):
        load_cases(path)


def test_unknown_oracle_action_rejected(tmp_path):
    row=json.loads(DATA.read_text().splitlines()[0]);row['allowed_actions']=['ignore_security']
    path=tmp_path/'invalid.jsonl';path.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='line'):
        load_cases(path)


def test_empty_denominators_not_reported_as_success():
    cases=load_cases(DATA)
    from decisionbound.transferbench import _score
    row=[(cases[2],'abstain')]
    result=_score(row)
    assert result['harmful_per_transfer'] is None
    assert result['harmful_per_conflict_opportunity'] is None


def test_cli_reproducibility_and_no_overwrite(tmp_path):
    target=tmp_path/'results.json'
    command=[sys.executable,str(ROOT/'scripts/evaluate_transferbench.py'),
             '--cases',str(DATA),'--seed','17','--output',str(target)]
    first=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert first.returncode==0,first.stderr
    before=target.read_bytes()
    report=json.loads(before)
    assert len(report['case_file_sha256'])==64
    assert len(report['policy_source_sha256'])==64
    second=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert second.returncode!=0
    assert target.read_bytes()==before


def test_no_model_or_network_runtime_imports():
    source=(ROOT/'src/decisionbound/transferbench.py').read_text()
    assert 'requests.' not in source and 'urlopen(' not in source
    assert 'subprocess' not in source and 'eval(' not in source
    assert set(METHODS)=={'no-transfer','full-transfer','tags-only','decisionbound'}
