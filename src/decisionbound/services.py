"""Application services connecting provenance, transfer and context ports."""
from __future__ import annotations
import re
import uuid
from pathlib import Path
from .context import SelectiveContextCompiler
from .identity import hash_evidence
from .models import ContextCapsule, ContextItem, Decision, Project, TransferResult
from .ports import ContextCompiler, DecisionRepository, TransferPolicy
from .transfer import ConservativeTransferPolicy


class EvidenceChanged(ValueError):
    """Source document no longer matches its reviewed SHA-256 digest."""


class DecisionService:
    """Reusable coordination service with dependency-injected policies.

    Args:
        repository: Persistent storage implementation.
        transfer_policy: Authority evaluator; defaults to conservative rules.
        compiler: Context selection policy; defaults to lexical budgeting.

    Custom applications can override either policy without modifying this
    service. Approval always checks the source evidence hash regardless of the
    chosen transfer policy.
    """
    def __init__(self, repository: DecisionRepository,
                 transfer_policy: TransferPolicy | None = None,
                 compiler: ContextCompiler | None = None):
        self.repository = repository
        # A plugin may tighten the outcome, never bypass hard approval and
        # cross-project authority gates defined by the host runtime.
        self.authority_gate = ConservativeTransferPolicy()
        self.transfer_policy = transfer_policy or self.authority_gate
        self.compiler = compiler or SelectiveContextCompiler()

    def discover_adr(self, project: Project, root: Path, relative: str,
                     scope: str = "project", owner: str | None = None) -> Decision:
        """Create an unapproved candidate from an ADR; never auto-approve it.

        An ADR must include sections titled ``Context`` and ``Decision``.
        External source text is treated as evidence, not a trusted instruction.
        """
        if scope not in ("personal", "project"):
            raise ValueError("Unsupported decision scope")
        if owner is not None and owner != project.owner:
            raise PermissionError("Decision owner cannot differ from registered project owner")
        # Use the vetted path resolver for all untrusted file names.
        from .identity import safe_relative_path
        source = safe_relative_path(root, relative).read_text(encoding="utf-8")
        headings = list(re.finditer(r"(?m)^##\s+(Context|Decision)\s*$", source, re.I))
        pieces: dict[str, str] = {}
        for i, heading in enumerate(headings):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(source)
            pieces[heading.group(1).lower()] = source[heading.end():end].strip()
        if not pieces.get("context") or not pieces.get("decision"):
            raise ValueError("ADR needs nonempty '## Context' and '## Decision' sections")
        evidence = hash_evidence(root, relative)
        decision = Decision(decision_id=f"dec_{uuid.uuid4().hex}",
                            source_project_id=project.project_id,
                            statement=pieces["decision"][:4000], rationale=pieces["context"][:4000],
                            evidence=evidence, scope=scope, owner=owner or project.owner)
        self.repository.add_decision(decision)
        return decision

    def approve(self, root: Path, decision_id: str, revision: int) -> Decision:
        """Approve only when the original file content is still identical.

        Raises:
            EvidenceChanged: Source was edited, replaced or removed.
        """
        decision = self.repository.get_decision(decision_id)
        if decision is None:
            raise KeyError(decision_id)
        try:
            actual = hash_evidence(root, decision.evidence.relative_path)
        except (OSError, ValueError) as exc:
            raise EvidenceChanged("Unable to verify source evidence") from exc
        if actual.sha256 != decision.evidence.sha256:
            raise EvidenceChanged("Source changed; new review required")
        return self.repository.set_decision_status(decision_id, "approved", revision)

    def transfer(self, target: Project, forbidden: set[str] | None = None) -> list[TransferResult]:
        """Evaluate all known decisions; never mutate target repository."""
        outcomes = []
        for decision in self.repository.list_decisions():
            base = self.authority_gate.evaluate(decision, target, forbidden or set())
            if base.action in ("abstain", "escalate"):
                outcomes.append(base)
                continue
            proposed = self.transfer_policy.evaluate(decision, target, forbidden or set())
            if proposed.decision_id != decision.decision_id or proposed.evidence_sha256 != decision.evidence.sha256:
                raise ValueError("Override changed decision identity or evidence")
            if proposed.action not in ("apply", "translate", "abstain", "escalate"):
                raise ValueError("Invalid custom transfer action")
            if base.action == "translate" and proposed.action == "apply":
                # Cross-project application needs an authorized human review.
                outcomes.append(base)
            else:
                outcomes.append(proposed)
        return outcomes

    def prepare(self, task: str, target: Project, max_tokens: int = 1200,
                forbidden: set[str] | None = None) -> ContextCapsule:
        """Compile the task and compatible approved principles into a capsule.

        Only ``apply``/``translate`` results contribute statements. An external
        authoritative policy can be included as a protected context item using
        the injectable ``compiler`` for task-specific contexts.
        """
        results = self.transfer(target, forbidden)
        selected = {r.decision_id for r in results if r.action in ("apply", "translate")}
        items = [ContextItem(key=d.decision_id, text=d.statement, source=f"{d.evidence.relative_path}#sha256={d.evidence.sha256}", priority=10)
                 for d in self.repository.list_decisions() if d.decision_id in selected]
        return self.compiler.compile(task, items, max_tokens)
