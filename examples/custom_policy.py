"""Example: override DecisionBound's transfer policy without bypassing authority.

Run from a source checkout with ``PYTHONPATH=src python examples/custom_policy.py``.
"""
from collections.abc import Iterable
from decisionbound import ConservativeTransferPolicy, Decision, Evidence, Project, TransferResult


class TeamVetoPolicy(ConservativeTransferPolicy):
    """Enforce team blocklist and preserve the conservative core guardrails."""
    def __init__(self, blocked: set[str]):
        self.blocked = blocked

    def evaluate(self, decision: Decision, target: Project,
                 forbidden: Iterable[str] = ()) -> TransferResult:
        """Apply the union of forbidden decisions before portability checks."""
        return super().evaluate(decision, target, set(forbidden) | self.blocked)


if __name__ == "__main__":
    d = Decision("avoid-unreviewed", "proj-a", "Prefer reviewed changes", "reliability",
                 Evidence("docs/adr.md", "sha256-example"), scope="personal", status="approved", owner="dev")
    target = Project("proj-b", "demo", ("python",), "dev")
    print(TeamVetoPolicy({"avoid-unreviewed"}).evaluate(d, target))
