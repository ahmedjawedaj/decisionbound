"""Versioned, serialization-friendly domain objects used by DecisionBound.

Models are intentionally immutable to keep approvals, transfers and run inputs
reproducible. Changes should create a new value rather than silently altering an
approved decision while another client is reading it.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Literal

DecisionStatus = Literal["candidate", "approved", "revoked"]
TransferAction = Literal["apply", "translate", "abstain", "escalate"]


@dataclass(frozen=True)
class Evidence:
    """Hash-linked source evidence for an engineering decision.

    Attributes:
        relative_path: Path relative to the originating repository. Absolute
            paths and paths traversing outside the project are not accepted.
        sha256: SHA-256 of the original source bytes at review time.
        kind: Evidence category, such as ``adr`` or ``human_review``.
    """
    relative_path: str
    sha256: str
    kind: str = "adr"


@dataclass(frozen=True)
class Project:
    """A logical project with an explicit scope and set of technologies.

    ``project_id`` is an identifier, **not** an authorization token. A client
    needs permission from the runtime even if it knows the ID.
    """
    project_id: str
    name: str
    technologies: tuple[str, ...] = ()
    owner: str = "local"


@dataclass(frozen=True)
class Decision:
    """An approved or unapproved engineering preference with provenance.

    ``scope`` may be ``project`` or ``personal``. Project-scoped decisions are
    not carried across repositories. Personal decisions may transfer only when
    their explicit applicability and the target's policies permit it.

    ``required_tags`` denotes all required target technologies. Empty means
    technology-agnostic. ``blocked_tags`` denotes explicit exclusions.
    """
    decision_id: str
    source_project_id: str
    statement: str
    rationale: str
    evidence: Evidence
    scope: Literal["project", "personal"] = "project"
    status: DecisionStatus = "candidate"
    required_tags: tuple[str, ...] = ()
    blocked_tags: tuple[str, ...] = ()
    owner: str = "local"
    revision: int = 1

    def to_dict(self) -> dict:
        """Return a JSON-safe representation without mutating the decision."""
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> Decision:
        """Restore a decision from a store record or a reviewed export."""
        clean = dict(record)
        clean["evidence"] = Evidence(**clean["evidence"])
        for k in ("required_tags", "blocked_tags"):
            clean[k] = tuple(clean.get(k, ()))
        return cls(**clean)


@dataclass(frozen=True)
class TransferResult:
    """Explainable decision about reuse in a target project.

    A transfer result is advisory; it is not permission to perform a write.
    """
    decision_id: str
    action: TransferAction
    reason: str
    evidence_sha256: str


@dataclass(frozen=True)
class ContextItem:
    """Candidate context material with a verifiable source pointer.

    ``protected`` items cannot be dropped to satisfy a token budget. If they
    do not fit the compiler raises :class:`ContextBudgetExceeded`.
    """
    key: str
    text: str
    source: str
    protected: bool = False
    priority: int = 0


@dataclass(frozen=True)
class ContextCapsule:
    """Immutable output of context selection for one task."""
    task: str
    items: tuple[ContextItem, ...]
    omitted_keys: tuple[str, ...]
    estimated_tokens: int
    max_tokens: int

    def to_dict(self) -> dict:
        """Serialize a capsule for CLI, API, or benchmark output."""
        return asdict(self)


@dataclass(frozen=True)
class WorkerResult:
    """A bounded worker result, separate from verified task completion."""
    status: Literal["prepared", "completed", "failed"]
    message: str
    observations: tuple[str, ...] = field(default_factory=tuple)
