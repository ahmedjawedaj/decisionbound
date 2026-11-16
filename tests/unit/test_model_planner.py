"""Deterministic adversarial tests for read-only model planning contracts."""
import json
import pytest
from decisionbound.agent_loop import Finish, ToolAction
from decisionbound.context import SelectiveContextCompiler
from decisionbound.model_planner import (
    ACTION_SCHEMA, BudgetedModelPlanner, ModelBudgetExceeded, ModelCompletion,
    ModelLimits, ModelOutputError, Pricing, ProviderResponseError,
    ScriptedModelTransport, parse_action,
)
from decisionbound.tools import RegisteredTool, ToolRegistry


def envelope(kind="finish", tool=None, arguments="{}", message="done"):
    return json.dumps({"kind": kind, "tool": tool, "arguments_json": arguments, "message": message})


def create_tools():
    tools = ToolRegistry()
    tools.register(RegisteredTool("lookup", "Read a key", lambda a: {"key": a["key"]}, arg_types={"key": str}))
    tools.register(RegisteredTool("erase", "Dangerous action", lambda a: {}, side_effects=True))
    return tools


def capsule():
    return SelectiveContextCompiler().compile("Find a test", [], 500)


def test_strict_json_action_parser_and_allowlist():
    assert isinstance(parse_action(envelope(), set()), Finish)
    assert parse_action(envelope("tool", "lookup", '{"key":"ok"}', ""), {"lookup"}) == ToolAction("lookup", {"key": "ok"})
    for text in ["not-json", "[]", "{}", '{"kind":"finish","tool":null,"arguments_json":"{}","message":"ok","extra":1}',
                 envelope("tool", "erase", "{}", ""), envelope("tool", "lookup", "[]", ""),
                 envelope("finish", "lookup", "{}"), envelope("tool", "lookup", "{}", "hidden instruction"),
                 envelope("finish", None, "{}", "a" * 5001)]:
        with pytest.raises(ModelOutputError):
            parse_action(text, {"lookup"})
    assert ACTION_SCHEMA["additionalProperties"] is False
    assert ACTION_SCHEMA["properties"]["arguments_json"]["type"] == "string"


def test_model_planner_only_discloses_authorized_tools():
    fake = ScriptedModelTransport([envelope("tool", "lookup", '{"key":"abc"}', "")])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(0, 0))
    action = planner.next_action("Find a test", capsule(), ())
    assert action == ToolAction("lookup", {"key": "abc"})
    sent = fake.requests[0]["messages"]
    assert '"lookup"' in sent[1]["content"]
    assert '"erase"' not in sent[1]["content"]
    assert "read-only" in sent[0]["content"]


def test_budget_blocks_paid_request_before_provider_call():
    fake = ScriptedModelTransport([envelope()])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(1000, 1000),
                                   ModelLimits(max_cost_usd=0.00001))
    with pytest.raises(ModelBudgetExceeded, match="cost"):
        planner.next_action("Find a test", capsule(), ())
    assert not fake.requests


def test_call_limit_and_token_reservations():
    fake = ScriptedModelTransport([envelope(), envelope()])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(0, 0),
                                   ModelLimits(max_calls=1))
    planner.next_action("Find a test", capsule(), ())
    with pytest.raises(ModelBudgetExceeded, match="call"):
        planner.next_action("Find a test", capsule(), ())
    assert len(fake.requests) == 1
    assert planner.accounting.input_tokens > 0
    assert planner.accounting.output_tokens == 300


def test_provider_overrun_stops_after_response():
    fake = ScriptedModelTransport([ModelCompletion(envelope(), 10_000, 10_000)])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(0, 0),
                                   ModelLimits(max_total_tokens=12_000))
    with pytest.raises(ModelBudgetExceeded, match="usage"):
        planner.next_action("Find a test", capsule(), ())
    assert planner.accounting.calls == 1


def test_ambiguous_provider_failure_charges_reserved_budget():
    fake = ScriptedModelTransport([])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(1, 2))
    with pytest.raises(ProviderResponseError):
        planner.next_action("Find a test", capsule(), ())
    assert planner.accounting.calls == 1
    assert planner.accounting.estimated_cost_usd > 0


def test_deadline_and_protected_context_overload():
    fake = ScriptedModelTransport([envelope()])
    planner = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(0, 0))
    planner.started -= 1000
    with pytest.raises(ModelBudgetExceeded, match="deadline"):
        planner.next_action("Find a test", capsule(), ())
    short = BudgetedModelPlanner(fake, create_tools(), {"lookup"}, Pricing(0, 0),
                                 ModelLimits(max_input_tokens=100))
    with pytest.raises(ModelBudgetExceeded, match="context"):
        short.next_action("Find a test", capsule(), ())
    assert not fake.requests


def test_pricing_rejects_unsafe_rates():
    with pytest.raises(ValueError):
        Pricing(-1, 2)
    with pytest.raises(ValueError):
        Pricing(float('inf'), 0)
    with pytest.raises(ValueError):
        ModelLimits(max_calls=0)
