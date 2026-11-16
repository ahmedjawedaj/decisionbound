"""Extension points for coding workers, test execution, and verification.

Subclass these interfaces rather than replacing security controls. Regardless
of override implementation, the host retains authority over Git path checks,
write approval, durable trace receipts and completion labels.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from .code_models import PatchProposal, PatchInspection, TestOutcome, Workspace


class PatchProposer(ABC):
    """Supply a candidate Git patch without performing any filesystem writes."""

    @abstractmethod
    def propose(self, task: str, source_files: dict[str, str]) -> PatchProposal:
        """Return a candidate patch; source is bounded and selected by the caller."""


class IsolatedTestRunner(ABC):
    """Execute a test command inside a disposable workspace.

    Implementations must honestly report their isolation kind; only a real
    isolated runner can contribute to independent acceptance verification.
    """

    @abstractmethod
    def run(self, workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome:
        """Produce a host-recorded test receipt without including raw source."""


class CodingVerifier(ABC):
    """Independently assess patch and test receipts after agent execution."""

    @abstractmethod
    def verify(self, run_id: str, inspection: PatchInspection, diff_sha256: str,
               tests: tuple[TestOutcome, ...]) -> tuple[bool, str]:
        """Return acceptance and explanation, never trust a worker's claim."""
