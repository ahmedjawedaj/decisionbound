"""Mocked model patch generation, strict schemas and provider spending caps."""
from __future__ import annotations
import json
import pytest
from decisionbound.code_provider import ModelPatchProposer
from decisionbound.model_planner import (ModelCompletion, ModelLimits, ModelBudgetExceeded,
                                         Pricing, ProviderResponseError, ScriptedModelTransport)
from tests.integration.test_code_workflow import PATCH


def raw_response(patch=PATCH, summary='Minimal change'):
    return json.dumps({'patch': patch, 'summary': summary})


def test_strict_single_use_model_patch_proposal():
    transport = ScriptedModelTransport([raw_response()])
    proposer = ModelPatchProposer(transport, Pricing(0.5, 2.0))
    result = proposer.propose('Fix the code', {'app.py': 'VALUE = 1\n'})
    assert result.patch == PATCH
    assert result.explanation == 'Minimal change'
    assert transport.requests[0]['schema']['required'] == ['patch', 'summary']
    assert proposer.accounted_cost_usd >= 0
    with pytest.raises(ModelBudgetExceeded, match='single-use'):
        proposer.propose('again', {'app.py': 'VALUE = 1\n'})


@pytest.mark.parametrize('payload', [
    'not json', '{}', '[]', json.dumps({'patch': PATCH, 'summary': '', 'extra': True}),
    json.dumps({'patch': 5, 'summary': ''}), json.dumps({'patch': PATCH, 'summary': 'x'*1001}),
])
def test_rejects_wrong_provider_patch_schema(payload):
    transport = ScriptedModelTransport([payload])
    with pytest.raises(ProviderResponseError):
        ModelPatchProposer(transport, Pricing(0, 0)).propose('Fix', {'app.py': 'a'})


def test_no_context_no_paid_call():
    transport = ScriptedModelTransport([raw_response()])
    with pytest.raises(ValueError):
        ModelPatchProposer(transport, Pricing(0, 0)).propose('Fix', {})
    assert len(transport.requests) == 0


def test_budget_preflight_prevents_provider_call():
    transport = ScriptedModelTransport([raw_response()])
    with pytest.raises(ModelBudgetExceeded):
        ModelPatchProposer(transport, Pricing(1000, 1000),
                           ModelLimits(max_calls=1, max_cost_usd=0.00001)).propose('Fix', {'app.py': 'abc'})
    assert transport.requests == []


def test_provider_usage_overrun_is_detected_and_not_retried():
    transport = ScriptedModelTransport([ModelCompletion(raw_response(), input_tokens=40000, output_tokens=50000)])
    proposer = ModelPatchProposer(transport, Pricing(10, 30),
                                 ModelLimits(max_calls=1, max_cost_usd=0.1, max_total_tokens=100000))
    with pytest.raises(ModelBudgetExceeded):
        proposer.propose('Fix', {'app.py': 'abc'})
    assert len(transport.requests) == 1
    assert proposer.accounted_cost_usd > 0.1


def test_provider_failure_is_never_retried():
    transport = ScriptedModelTransport([])
    proposer = ModelPatchProposer(transport, Pricing(0, 0))
    with pytest.raises(ProviderResponseError):
        proposer.propose('Fix', {'app.py': 'abc'})
    assert len(transport.requests) == 1
    with pytest.raises(ModelBudgetExceeded):
        proposer.propose('Fix', {'app.py': 'abc'})


def test_approved_context_is_injected_separately_from_source_files():
    from decisionbound.models import ContextCapsule, ContextItem
    capsule = ContextCapsule('task', (ContextItem('adr', 'Avoid global state', 'docs/adr/1.md', True, 10),),
                             (), 10, 1200)
    transport = ScriptedModelTransport([raw_response()])
    proposer = ModelPatchProposer(transport, Pricing(0, 0), context=capsule)
    proposer.propose('change', {'app.py': 'VALUE = 1\n'})
    payload = json.loads(transport.requests[0]['messages'][1]['content'])
    assert payload['approved_context'][0]['protected'] is True
    assert payload['approved_context'][0]['text'] == 'Avoid global state'
    assert payload['selected_files'] == {'app.py': 'VALUE = 1\n'}
