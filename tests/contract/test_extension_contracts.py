"""Reusable behavioral contract for storage and extension implementers."""
from dataclasses import replace
import pytest
from decisionbound.models import Project, Decision, Evidence
from decisionbound.ports import DecisionRepository, TransferPolicy, ContextCompiler, AgentWorker, TaskVerifier
from decisionbound.storage import SQLiteRepository, RevisionConflict
from decisionbound.context import ContextBudgetExceeded, SelectiveContextCompiler
from decisionbound.models import ContextItem


def test_abstract_ports_reject_missing_implementation():
    for abstract in (DecisionRepository, TransferPolicy, ContextCompiler, AgentWorker, TaskVerifier):
        with pytest.raises(TypeError):
            abstract()


def test_repository_port_conformance(tmp_path):
    """Copy this behavioral contract into external Postgres adapter test suites."""
    repository = SQLiteRepository(tmp_path / "one.db")
    project = Project("prj_test", "example", ("python",), "test")
    repository.add_project(project)
    assert repository.get_project(project.project_id) == project
    with pytest.raises(ValueError):
        repository.add_project(replace(project, name="different"))
    record = Decision("dec_test", project.project_id, "Rules", "Because", Evidence("adr.md", "sha"))
    repository.add_decision(record)
    assert repository.get_decision(record.decision_id).status == "candidate"
    approved = repository.set_decision_status("dec_test", "approved", expected_revision=1)
    assert approved.status == "approved"
    assert approved.revision == 2
    with pytest.raises(RevisionConflict):
        repository.set_decision_status("dec_test", "revoked", expected_revision=1)


def test_context_port_protects_owner_constraints():
    compiler = SelectiveContextCompiler()
    protected = ContextItem("approved-owner-policy", "Do not export source", "human-reviewed ADR", protected=True)
    assert protected in compiler.compile("task", [protected], 100).items
    with pytest.raises(ContextBudgetExceeded):
        compiler.compile("task", [protected], 2)
