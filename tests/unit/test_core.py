"""Contract-focused tests for transfer, context and extension overrides."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import pytest
from decisionbound import (ContextItem, Decision, Evidence, Project, SQLiteRepository,
                           ConservativeTransferPolicy, SelectiveContextCompiler)
from decisionbound.context import ContextBudgetExceeded
from decisionbound.ports import TransferPolicy, ContextCompiler
from decisionbound.services import DecisionService, EvidenceChanged
from decisionbound.storage import RevisionConflict
from decisionbound.identity import init_project, hash_evidence, safe_relative_path, load_project
from decisionbound.runtime import Supervisor, OfflinePreparationWorker, PreparationVerifier
from decisionbound.models import TransferResult
from decisionbound.skills import SkillRegistry
from decisionbound.tools import ToolRegistry, RegisteredTool, ToolPermissionDenied


@pytest.fixture
def store(tmp_path):
    return SQLiteRepository(tmp_path / "private" / "state.sqlite3")


@pytest.fixture
def project(tmp_path, store):
    return init_project(tmp_path, store, name="target", technologies=("python",), owner="dev-a")


def create_decision(store, source_id="source", status="approved", scope="personal", **overrides):
    d = Decision("d1", source_id, "Keep domain logic isolated", "unit testing",
                 Evidence("docs/adr/01.md", "abc123"), scope=scope, status=status,
                 required_tags=("python",), owner="dev-a", **overrides)
    store.add_decision(d)
    return d


def test_project_identity_collision(store, tmp_path):
    p = init_project(tmp_path, store, "one", owner="dev-a")
    assert load_project(tmp_path, store) == p
    with pytest.raises(ValueError, match="collision"):
        store.add_project(replace(p, name="fake"))


def test_project_manifest_tamper_rejected(store, tmp_path, project):
    manifest = tmp_path / ".decisionbound" / "project.yaml"
    manifest.write_text(manifest.read_text().replace("dev-a", "attacker"))
    with pytest.raises(PermissionError):
        load_project(tmp_path, store)


def test_source_path_traversal_rejected(tmp_path):
    (tmp_path / "file.md").write_text("hello")
    for name in ("../outside.md", "/etc/passwd"):
        with pytest.raises(ValueError):
            safe_relative_path(tmp_path, name)
    assert hash_evidence(tmp_path, "file.md").sha256


def test_cross_project_personal_translate(store, project):
    d = create_decision(store)
    assert ConservativeTransferPolicy().evaluate(d, project).action == "translate"


def test_cross_project_scope_abstains(store, project):
    d = create_decision(store, scope="project")
    assert ConservativeTransferPolicy().evaluate(d, project).action == "abstain"


def test_wrong_owner_abstains(store, project):
    d = create_decision(store)
    assert ConservativeTransferPolicy().evaluate(d, replace(project, owner="other")).action == "abstain"


def test_required_tag_missing_abstains(store, project):
    d = create_decision(store)
    assert ConservativeTransferPolicy().evaluate(d, replace(project, technologies=("go",))).action == "abstain"


def test_unapproved_abstains(store, project):
    d = create_decision(store, status="candidate")
    assert ConservativeTransferPolicy().evaluate(d, project).action == "abstain"


def test_explicit_target_conflict_escalates(store, project):
    d = create_decision(store)
    assert ConservativeTransferPolicy().evaluate(d, project, forbidden={d.decision_id}).action == "escalate"


def test_decision_revision_cas(store):
    d = create_decision(store, status="candidate")
    updated = store.set_decision_status(d.decision_id, "approved", 1)
    assert updated.revision == 2
    with pytest.raises(RevisionConflict):
        store.set_decision_status(d.decision_id, "revoked", 1)


def test_revocation_cannot_be_silently_undone(store):
    d = create_decision(store)
    store.set_decision_status(d.decision_id, "revoked", 1)
    with pytest.raises(ValueError):
        store.set_decision_status(d.decision_id, "approved", 2)


def test_adr_provenance_requires_review(store, project, tmp_path):
    f = tmp_path / "adr.md"
    f.write_text("## Context\nSystem must be clear.\n## Decision\nUse typed adapters.\n")
    svc = DecisionService(store)
    d = svc.discover_adr(project, tmp_path, "adr.md")
    assert d.status == "candidate"
    assert svc.approve(tmp_path, d.decision_id, d.revision).status == "approved"


def test_adr_approval_rejects_changed_source(store, project, tmp_path):
    f = tmp_path / "adr.md"
    f.write_text("## Context\nReason.\n## Decision\nChoice.\n")
    svc = DecisionService(store)
    d = svc.discover_adr(project, tmp_path, "adr.md")
    f.write_text("## Context\nReason.\n## Decision\nChanged.\n")
    with pytest.raises(EvidenceChanged):
        svc.approve(tmp_path, d.decision_id, d.revision)


def test_context_never_drops_protected():
    compiler = SelectiveContextCompiler()
    c = compiler.compile("bug", [ContextItem("security", "Never expose secrets.", "ADR", protected=True),
                                 ContextItem("long", "big " * 1000, "notes")], 100)
    assert [i.key for i in c.items] == ["security"]
    assert c.omitted_keys == ("long",)


def test_context_exceeds_budget_cleanly():
    with pytest.raises(ContextBudgetExceeded):
        SelectiveContextCompiler().compile("task", [ContextItem("x", "a" * 3000, "ADR", True)], 12)


def test_context_duplicate_keys_rejected():
    item = ContextItem("x", "hello", "a")
    with pytest.raises(ValueError):
        SelectiveContextCompiler().compile("task", [item, item], 50)


def test_custom_transfer_policy_override(store, project):
    class CustomPolicy(TransferPolicy):
        def evaluate(self, decision, target, forbidden=()):
            return TransferResult(decision.decision_id, "abstain", "Custom governance veto", decision.evidence.sha256)
    create_decision(store)
    svc = DecisionService(store, transfer_policy=CustomPolicy())
    assert svc.transfer(project)[0].reason == "Custom governance veto"


def test_custom_compiler_override():
    class CustomCompiler(ContextCompiler):
        def compile(self, task, items, max_tokens):
            return SelectiveContextCompiler().compile(task, items, max_tokens)
    assert CustomCompiler().compile("task", [], 20).task == "task"


def test_tool_registry_never_grants_unlisted_call():
    tools = ToolRegistry()
    tools.register(RegisteredTool("read", "Safe read", lambda args: {"path": args["path"]}, arg_types={"path": str}))
    tools.register(RegisteredTool("write", "Dangerous write", lambda args: {"ok": True}, True))
    assert tools.call("read", {"path": "a"}, {"read"})["path"] == "a"
    with pytest.raises(ToolPermissionDenied):
        tools.call("write", {}, {"write"})
    with pytest.raises(ToolPermissionDenied):
        tools.call("unknown", {}, {"unknown"})


def test_registered_read_tool_is_refused_when_the_run_allowlist_excludes_it():
    """A tool that exists and is harmless still needs a place on this run's allowlist."""
    ran = []
    tools = ToolRegistry()
    tools.register(RegisteredTool("read", "Safe read", lambda args: ran.append(1) or {"ok": True}))
    tools.register(RegisteredTool("other", "Another read", lambda args: {"ok": True}))
    with pytest.raises(ToolPermissionDenied, match="not permitted"):
        tools.call("read", {}, {"other"})
    assert ran == []
    assert tools.call("read", {}, {"read"}) == {"ok": True}


def test_skills_lazy_loading(tmp_path):
    folder = tmp_path / "skills" / "unit-testing"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Unit Testing\nMany more instructions...\n")
    registry = SkillRegistry([tmp_path / "skills"])
    skills = registry.discover()
    assert skills[0].name == "unit-testing"
    assert "Many more" not in skills[0].summary
    assert "Many more" in registry.load(skills[0])


def test_offline_run_does_not_claim_completed(store):
    capsule = SelectiveContextCompiler().compile("task", [], 50)
    result = Supervisor(store).run("task", capsule)
    assert result["preparation_verified"] is True
    assert result["task_completed"] is False
    assert len(store.events(result["run_id"])) == 3
    with pytest.raises(ValueError, match="already exists"):
        Supervisor(store).run("task", capsule, run_id=result["run_id"])


def test_readonly_agent_loop_search_and_budget(tmp_path):
    from decisionbound.agent_loop import (repository_readonly_tools, BoundedReadOnlyAgent,
                                          SearchThenFinishPlanner, Planner, ToolAction)
    (tmp_path / "main.py").write_text("# trusted visible pattern\nprint('hello')\n")
    (tmp_path / ".env").write_text("trusted=VERY_SECRET")
    context = SelectiveContextCompiler().compile("trusted", [], 120)
    tools = repository_readonly_tools(tmp_path)
    result = BoundedReadOnlyAgent(SearchThenFinishPlanner(), tools, {"search_project"}, max_steps=3).run("trusted", context)
    assert result.status == "prepared"
    assert "main.py" in result.observations[0]
    assert "VERY_SECRET" not in result.observations[0]
    with pytest.raises(PermissionError):
        tools.call("read_file", {"path": ".env"}, {"read_file"})
    with pytest.raises(RuntimeError, match="budget"):
        BoundedReadOnlyAgent(SearchThenFinishPlanner(), tools, {"search_project"}, max_steps=1).run("trusted", context)


def test_readonly_loop_refuses_model_requested_write(tmp_path):
    from decisionbound.agent_loop import BoundedReadOnlyAgent, Planner, ToolAction
    tools = ToolRegistry()
    tools.register(RegisteredTool("delete_everything", "danger", lambda _: {"ok": True}, side_effects=True))
    class MaliciousPlanner(Planner):
        def next_action(self, task, context, observations):
            return ToolAction("delete_everything", {})
    context = SelectiveContextCompiler().compile("task", [], 20)
    with pytest.raises(ToolPermissionDenied):
        BoundedReadOnlyAgent(MaliciousPlanner(), tools, {"delete_everything"}).run("task", context)


def test_first_denied_tool_call_ends_the_run_and_the_planner_is_not_asked_again():
    """Recorded decision: an unattended read-only run has nobody to ask, so one denial
    is final. Retrying would only give a model more tries at the permission boundary."""
    from decisionbound.agent_loop import BoundedReadOnlyAgent, Finish, Planner, ToolAction
    ran = []
    tools = ToolRegistry()
    tools.register(RegisteredTool("read", "Safe read", lambda a: ran.append("read") or {"ok": True}))
    tools.register(RegisteredTool("delete_everything", "danger", lambda _: ran.append("delete") or {}, side_effects=True))
    asked = []
    events = []

    class RetryingPlanner(Planner):
        def next_action(self, task, context, observations):
            asked.append(len(observations))
            return [ToolAction("delete_everything", {}), ToolAction("read", {}), Finish("done")][len(asked) - 1]

    context = SelectiveContextCompiler().compile("task", [], 20)
    agent = BoundedReadOnlyAgent(RetryingPlanner(), tools, {"read", "delete_everything"},
                                 on_event=lambda name, payload: events.append(name))
    with pytest.raises(ToolPermissionDenied):
        agent.run("task", context)
    assert asked == [0] and ran == []  # asked once, and neither tool ran
    assert events == ["tool_invocation"]  # the attempt is logged, no observation exists


def test_override_cannot_bypass_unapproved_or_project_scope(store, project):
    class UnsafePolicy(TransferPolicy):
        def evaluate(self, decision, target, forbidden=()):
            return TransferResult(decision.decision_id, "apply", "unsafe override", decision.evidence.sha256)
    create_decision(store, scope="project")
    service = DecisionService(store, UnsafePolicy())
    assert service.transfer(project)[0].action == "abstain"


def test_override_cannot_upgrade_cross_project_translate_to_apply(store, project):
    class AggressivePolicy(TransferPolicy):
        def evaluate(self, decision, target, forbidden=()):
            return TransferResult(decision.decision_id, "apply", "too confident", decision.evidence.sha256)
    create_decision(store)
    service = DecisionService(store, AggressivePolicy())
    assert service.transfer(project)[0].action == "translate"


def test_adr_cannot_claim_foreign_owner(store, project, tmp_path):
    (tmp_path / "adr.md").write_text("## Context\nReason.\n## Decision\nChoice.\n")
    with pytest.raises(PermissionError):
        DecisionService(store).discover_adr(project, tmp_path, "adr.md", scope="personal", owner="other")


def test_tool_schema_rejects_extra_missing_and_wrong_types():
    from decisionbound.tools import ToolSchemaError
    tools = ToolRegistry()
    tools.register(RegisteredTool("read", "Read a path", lambda a: {"path": a["path"]}, arg_types={"path": str}))
    for args in ({}, {"path": 123}, {"path": "ok", "secret": "injected"}):
        with pytest.raises(ToolSchemaError):
            tools.call("read", args, {"read"})
    assert tools.describe({"read"})[0]["parameters"] == {"path": "str"}
    assert not tools.describe(set())
