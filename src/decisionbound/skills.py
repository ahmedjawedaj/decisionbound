"""Low-cost, trust-aware discovery of project and personal skill manifests."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    """A discoverable skill descriptor; content is loaded only on selection."""
    name: str
    path: Path
    summary: str


class SkillRegistry:
    """Discover ``SKILL.md`` files without loading full text into context.

    Skill files are *untrusted content*, not executable or policy-authoritative
    instructions. Selection does not grant shell/network/tool permissions.
    """
    def __init__(self, roots: list[Path]):
        self.roots = roots

    def discover(self) -> list[Skill]:
        """Return metadata only, sorted by name and with duplicate names removed."""
        skills: dict[str, Skill] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for file in sorted(root.glob("*/SKILL.md")):
                if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
                    continue
                # A summary is loaded, not the entire skill body.
                with file.open(encoding="utf-8") as f:
                    head = f.read(512)
                first = next((line.strip("# ") for line in head.splitlines() if line.startswith("#")), file.parent.name)
                skills.setdefault(file.parent.name, Skill(file.parent.name, file, first[:120]))
        return sorted(skills.values(), key=lambda s: s.name)

    def load(self, skill: Skill, max_chars: int = 12000) -> str:
        """Read a selected skill with a size cap; no script execution."""
        if skill.path.is_symlink():
            raise ValueError("Symlinked skills are refused")
        if skill.path.stat().st_size > max_chars:
            raise ValueError("Skill exceeds the configured context cap")
        return skill.path.read_text(encoding="utf-8")
