"""Stable extension contracts for alternate storage, policies and workers.

Prefer implementing these ports instead of subclassing private concrete
classes. Every method documents expected behavior; contract tests supplied
under ``tests/contract`` can be reused by third-party adapters.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable
from .models import ContextCapsule, ContextItem, Decision, Project, TransferResult, WorkerResult


class DecisionRepository(ABC):
    """Persistence boundary for versioned projects, decisions and profiles.

    Implementations **must** reject stale revision updates and must never
    silently promote a candidate decision to approved status.
    """

    @abstractmethod
    def add_project(self, project: Project) -> None:
        """Persist a project or reject a conflicting project identifier."""

    @abstractmethod
    def get_project(self, project_id: str) -> Project | None:
        """Return a project by identifier, or ``None`` if absent."""

    @abstractmethod
    def add_decision(self, decision: Decision) -> None:
        """Persist a new candidate decision with its immutable evidence."""

    @abstractmethod
    def get_decision(self, decision_id: str) -> Decision | None:
        """Look up a decision without changing its approval status."""

    @abstractmethod
    def list_decisions(self, project_id: str | None = None) -> list[Decision]:
        """Return decisions in a deterministic order."""

    @abstractmethod
    def set_decision_status(self, decision_id: str, status: str, expected_revision: int) -> Decision:
        """Atomically update status or reject a stale revision."""


class TransferPolicy(ABC):
    """Overrideable compatibility and authority checks for project transfer."""

    @abstractmethod
    def evaluate(self, decision: Decision, target: Project, forbidden: Iterable[str] = ()) -> TransferResult:
        """Choose apply/translate/abstain/escalate with a human-readable reason."""


class ContextCompiler(ABC):
    """A swappable strategy that selects task-relevant information."""

    @abstractmethod
    def compile(self, task: str, items: Iterable[ContextItem], max_tokens: int) -> ContextCapsule:
        """Produce a bounded context capsule; never silently omit protected inputs."""


class AgentWorker(ABC):
    """Limited execution adapter; dispatchers must not assume completion is verified."""

    @abstractmethod
    def run(self, task: str, context: ContextCapsule) -> WorkerResult:
        """Execute one bounded step or task without exceeding assigned permissions."""


class TaskVerifier(ABC):
    """Independent outcome checker for a worker-produced artifact."""

    @abstractmethod
    def verify(self, task: str, result: WorkerResult) -> bool:
        """Return objective success based on external evidence, not self-report."""
