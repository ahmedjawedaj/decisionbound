"""Bounded read-only tool loop with pluggable planning, suitable for dry runs.

This is an *execution kernel*, not a generative coding agent. Providers can
implement Planner.next_action, but the host always enforces allowlists, effects,
step limits and observation caps independent of any model response.
"""
from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any
import hashlib
from .identity import safe_relative_path
from .models import ContextCapsule, WorkerResult
from .ports import AgentWorker
from .tools import RegisteredTool, ToolRegistry


@dataclass(frozen=True)
class ToolAction:
    """Strict action proposed by a planner and revalidated by the runtime."""
    name: str
    arguments: dict


@dataclass(frozen=True)
class Finish:
    """Planner's terminal message; not independently verified task completion."""
    message: str


class Planner(ABC):
    """Overrideable decision interface for one action at a time.

    A model planner should validate provider output against a strict schema
    before returning a ToolAction or Finish. Never execute raw model text as
    shell commands or Python source.
    """

    @abstractmethod
    def next_action(self, task: str, context: ContextCapsule,
                    observations: tuple[str, ...]) -> ToolAction | Finish:
        """Propose one allowlisted tool invocation or a terminal response."""


class BoundedReadOnlyAgent(AgentWorker):
    """Run a planner under host-controlled tool, step and observation budgets.

    Args:
        planner: Provider-independent planning implementation.
        tools: Registry of strictly typed host tool handlers.
        allowed_tools: Explicit capability names allowed for this run.
        max_steps: Hard upper bound on tool calls, from 1 to 25.
        max_observation_chars: Per-result cap before returning to planner.

    Side effects:
        None by default. Every call uses ``allow_writes=False`` regardless
        of whether the planner asks for a side-effecting tool.
    """

    def __init__(self, planner: Planner, tools: ToolRegistry, allowed_tools: set[str],
                 max_steps: int = 5, max_observation_chars: int = 3000,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None):
        if not 1 <= max_steps <= 25 or not 100 <= max_observation_chars <= 20000:
            raise ValueError("Unsafe or invalid agent loop limits")
        self.planner = planner
        self.tools = tools
        self.allowed_tools = frozenset(allowed_tools)
        self.max_steps = max_steps
        self.max_observation_chars = max_observation_chars
        self.on_event = on_event

    def run(self, task: str, context: ContextCapsule) -> WorkerResult:
        """Execute a bounded loop and return observations without claiming verification.

        Raises:
            ToolPermissionDenied: The planner selected a disallowed tool.
            RuntimeError: Maximum steps were exhausted without a Finish.
        """
        observations: list[str] = []
        for _ in range(self.max_steps):
            action = self.planner.next_action(task, context, tuple(observations))
            if isinstance(action, Finish):
                return WorkerResult("prepared", action.message, tuple(observations))
            if not isinstance(action, ToolAction):
                raise TypeError("Planner output must be ToolAction or Finish")
            if self.on_event:
                # Never store raw arguments or tool output in the global event ledger.
                self.on_event("tool_invocation", {"name": action.name,
                    "argument_keys": sorted(action.arguments),
                    "arguments_sha256": hashlib.sha256(json.dumps(action.arguments, sort_keys=True).encode()).hexdigest()})
            result = self.tools.call(action.name, action.arguments, set(self.allowed_tools), allow_writes=False)
            rendered = json.dumps({"tool": action.name, "result": result}, sort_keys=True)
            observation = rendered[:self.max_observation_chars]
            observations.append(observation)
            if self.on_event:
                self.on_event("tool_observation", {"name": action.name, "size": len(observation),
                    "sha256": hashlib.sha256(observation.encode()).hexdigest(), "truncated": len(rendered) > len(observation)})
        raise RuntimeError("Agent loop exhausted its tool-step budget")


_EXCLUDED = {".git", ".decisionbound", "node_modules", ".venv", "__pycache__"}
_SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json", "id_rsa", "id_ed25519"}
_SUPPORTED = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".ts", ".tsx", ".go", ".rs"}


def repository_readonly_tools(root: Path, max_file_bytes: int = 512_000) -> ToolRegistry:
    """Create safe local file inspection/search tools for a single project root.

    This is a bounded inspection utility, not a secure sandbox against code
    executing with the same operating-system permissions as the process.
    """
    base = root.resolve(strict=True)
    registry = ToolRegistry()

    def safe_file(path: str) -> Path:
        """Deny hidden credential names and unsupported/binary file types."""
        candidate = Path(path)
        if (any(part in _EXCLUDED or part.startswith(".") for part in candidate.parts)
                or candidate.name in _SECRET_NAMES
                or any((base / Path(*candidate.parts[:index])).is_symlink()
                       for index in range(1, len(candidate.parts) + 1))):
            raise PermissionError("Protected repository path")
        target = safe_relative_path(base, path)
        if target.suffix.lower() not in _SUPPORTED or target.stat().st_size > max_file_bytes:
            raise PermissionError("File type or size not supported")
        return target

    def read(args: dict) -> dict:
        """Return a capped excerpt of a reviewed local source file."""
        path = args.get("path")
        if not isinstance(path, str):
            raise ValueError("read_file expects string path")
        file = safe_file(path)
        # At most 200 lines and 6000 chars are returned, independent of caller.
        return {"path": path, "text": "\n".join(file.read_text(encoding="utf-8", errors="replace").splitlines()[:200])[:6000]}

    def search(args: dict) -> dict:
        """Bounded lexical search excluding hidden/secret and large files."""
        term = args.get("query")
        if not isinstance(term, str) or not 2 <= len(term) <= 120:
            raise ValueError("search_project expects a 2–120 char query")
        results = []
        count = 0
        inspected = 0
        for file in sorted(base.rglob("*")):
            inspected += 1
            if len(results) >= 10 or count >= 500 or inspected >= 2000:
                break
            if not file.is_file() or any(part in _EXCLUDED for part in file.relative_to(base).parts):
                continue
            relative = str(file.relative_to(base))
            try:
                vetted = safe_file(relative)
            except (ValueError, PermissionError, FileNotFoundError):
                continue
            count += 1
            for number, line in enumerate(vetted.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if term.casefold() in line.casefold():
                    results.append({"file": relative, "line": number, "excerpt": line[:300]})
                    break
        return {"query": term, "results": results, "files_scanned": count}

    registry.register(RegisteredTool("read_file", "Read a capped repository file excerpt", read, arg_types={"path": str}))
    registry.register(RegisteredTool("search_project", "Lexical search through safe repository files", search, arg_types={"query": str}))
    return registry


class SearchThenFinishPlanner(Planner):
    """Deterministic demo planner used to exercise a tool loop offline.

    It is not a language model or a substitute for a trained agent planner.
    """
    def next_action(self, task: str, context: ContextCapsule, observations: tuple[str, ...]) -> ToolAction | Finish:
        """Search once, then return a user-readable observation summary."""
        if observations:
            return Finish("Read-only repository search completed; task remains unverified")
        return ToolAction("search_project", {"query": task[:120]})
