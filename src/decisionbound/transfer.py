"""Safe-by-default transfer decisions with explicit project authority."""
from __future__ import annotations
from collections.abc import Iterable
from .models import Decision, Project, TransferResult
from .ports import TransferPolicy


class ConservativeTransferPolicy(TransferPolicy):
    """Deterministic transfer policy suitable for auditable baseline studies.

    It does **not** infer that a shared Git repository means shared authority.
    Cross-project personal transfer requires the same explicitly named owner;
    project-scoped decisions never transfer. Higher-priority target policy
    conflicts are treated as escalation rather than opportunities to guess.

    Subclass and override :meth:`evaluate` to implement an experimental model
    or a domain-specific organization policy. Test subclasses against the
    authority and negative-transfer contract suite before deployment.
    """

    def evaluate(self, decision: Decision, target: Project, forbidden: Iterable[str] = ()) -> TransferResult:
        """Return a reasoned action without changing any project files.

        Args:
            decision: Approved, evidence-linked candidate for reuse.
            target: Target project and its declared technology tags.
            forbidden: Authoritative blocked decision IDs set by the target
                owner; never populated from an untrusted retrieved document.

        Returns:
            One of ``apply``, ``translate``, ``abstain`` or ``escalate``.
        """
        def result(action: str, reason: str) -> TransferResult:
            return TransferResult(decision.decision_id, action, reason, decision.evidence.sha256)

        if decision.status != "approved":
            return result("abstain", "Decision has not been approved or was revoked")
        if decision.decision_id in set(forbidden):
            return result("escalate", "Target owner policy forbids this decision")
        if decision.source_project_id != target.project_id:
            if decision.scope != "personal":
                return result("abstain", "Project-scoped decision cannot transfer")
            if target.owner != decision.owner:
                return result("abstain", "Personal decision owner differs from target owner")
        tags = set(target.technologies)
        if tags.intersection(decision.blocked_tags):
            return result("abstain", "Target matches an explicit decision exclusion")
        if not set(decision.required_tags).issubset(tags):
            return result("abstain", "Required technology constraints not satisfied")
        if decision.source_project_id == target.project_id:
            return result("apply", "Approved decision from the same project")
        return result("translate", "Personal principle is compatible but needs target-repository adaptation")
