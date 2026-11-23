"""Immutable contracts for isolated coding runs and their evidence.

These values are transport-independent. A generated patch is a proposal, not
permission to edit a checkout or proof that the requested task was completed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class PatchProposal:
    """Unified Git patch plus a short, untrusted explanation from a worker.

    The patch may contain proprietary source code; durable event logs store only
    hashes and counts. Use the private artifact store to persist its content.
    """
    patch: str
    explanation: str = ""


@dataclass(frozen=True)
class PatchInspection:
    """Host-validated patch scope before a write is authorized."""
    paths: tuple[str, ...]
    added_lines: int
    removed_lines: int
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Workspace:
    """One disposable, detached Git worktree for a single run."""
    path: str
    repository_root: str
    base_commit: str
    run_id: str


@dataclass(frozen=True)
class TestOutcome:
    """Receipt returned by a restricted test executor, never by the model.

    ``kind`` distinguishes a real isolated process from a deterministic mock.
    A mock may validate control flow, but cannot establish real test success.
    """
    __test__ = False  # Public domain object, not a pytest test class.

    kind: Literal["docker", "mock", "static"]
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_sha256: str
    stderr_sha256: str
    output_truncated: bool = False
    image: str = ""
    image_digest: str = ""
    daemon_endpoint: str = ""

    def to_dict(self) -> dict:
        """Serialize a test receipt without storing raw test output.

        ``image`` is the image reference the sandbox was given. ``image_digest`` is the ``sha256:``
        digest that reference pins, or empty when it is a mutable tag. ``daemon_endpoint`` is the
        ``unix://`` daemon the container CLI was told to use, or ``default`` for its own default.
        The three keys are left out when empty, so receipts that carry none serialize and hash
        exactly as before.
        """
        value = asdict(self)
        value["command"] = list(self.command)
        for key in ("image", "image_digest", "daemon_endpoint"):
            if not value[key]:
                del value[key]
        return value


@dataclass(frozen=True)
class CodingOutcome:
    """Stable receipt for a bounded patch attempt.

    ``acceptance_verified`` is true only after host-side evidence and a real
    isolated test runner pass. ``task_completed`` remains false until stronger
    task-specific semantic acceptance or human approval is provided.
    """
    run_id: str
    base_commit: str
    patch_sha256: str
    diff_sha256: str
    changed_paths: tuple[str, ...]
    patch_artifact: str
    acceptance_verified: bool
    task_completed: bool
    verification_reason: str
    tests: tuple[TestOutcome, ...]
    contract_verified: bool = False
    contract_sha256: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-compatible, versioned run receipt for clients."""
        value = asdict(self)
        value["schema_version"] = "1.0"
        value["changed_paths"] = list(self.changed_paths)
        value["tests"] = [test.to_dict() for test in self.tests]
        return value
