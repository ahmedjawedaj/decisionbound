"""Bounded read-only tool loop with pluggable planning, suitable for dry runs.

This is an *execution kernel*, not a generative coding agent. Providers can
implement Planner.next_action, but the host always enforces allowlists, effects,
step limits and observation caps independent of any model response.
"""
from __future__ import annotations
import json
import math
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, NoReturn
import fnmatch
import hashlib
from .identity import safe_relative_path
from .models import ContextCapsule, WorkerResult
from .ports import AgentWorker
from .tools import RegisteredTool, ToolRegistry


UNTRUSTED_BEGIN = "<<<UNTRUSTED_TOOL_OUTPUT>>>"
UNTRUSTED_END = "<<<END_UNTRUSTED_TOOL_OUTPUT>>>"
_MARKER_RUN = re.compile(r"<{3,}|>{3,}")
_TOOL_PREFIX = re.compile(r'^\{"tool": "([A-Za-z0-9_.:-]{1,64})"')


class RepeatedCallError(RuntimeError):
    """The planner proposed the same tool call too many times in a row."""


class AgentDeadlineExceeded(RuntimeError):
    """The loop's own wall-clock deadline passed before the next planner or tool call."""


def neutralise_markers(text: str) -> str:
    """Break up any run of three or more ``<`` or ``>`` so content cannot forge a marker."""
    return _MARKER_RUN.sub(lambda m: " ".join(m.group(0)), text)


def render_untrusted_observation(observation: str, allowed_tools: frozenset[str] | set[str],
                                 limit: int = 3000) -> str:
    """Present one stored observation to a model as marked, escaped, visibly cut data.

    The tool name sits outside the markers and is shown only when it is one of
    ``allowed_tools``. The content between the markers is the stored observation
    with marker-like runs neutralised and cut to ``limit`` characters. A note
    after the closing marker says when the content was cut, either here or by
    the loop's own cap (the loop stores JSON objects, so text that starts with ``{`` but is not valid JSON was cut). The stored
    observation, its hash and the event ledger are not changed by this.
    """
    text = str(observation)
    match = _TOOL_PREFIX.match(text)
    tool = match.group(1) if match and match.group(1) in allowed_tools else "unknown"
    cut_by_loop = False
    if text.startswith("{"):
        try:
            json.loads(text)
        except ValueError:
            cut_by_loop = True
    body = neutralise_markers(text)
    cut_here = len(body) > limit
    body = body[:limit]
    note = ""
    if cut_here or cut_by_loop:
        note = f"\n[observation was cut, showing at most {limit} characters, so it may end mid-value]"
    return f"tool: {tool}\n{UNTRUSTED_BEGIN}\n{body}\n{UNTRUSTED_END}{note}"


def render_untrusted_text(text: str, limit: int | None = None) -> str:
    """Fence stored free text (a decision statement, an ADR excerpt) as data for a model.

    Marker-like runs are neutralised first, so the result always holds one opening and one
    closing marker, and the markers are the ones used for tool observations. With ``limit``
    the neutralised text is cut to that many characters and a note after the closing marker
    says so. The stored text is not changed.
    Use it only for the copy a model reads, never for stored or hashed records.
    """
    body = neutralise_markers(str(text))
    note = ""
    if limit is not None and len(body) > limit:
        body = body[:limit]
        note = f"\n[text was cut, showing at most {limit} characters, so it may end mid-value]"
    return f"{UNTRUSTED_BEGIN}\n{body}\n{UNTRUSTED_END}{note}"


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
        max_identical_calls: How many times in a row the same tool and
            arguments may run, from 1 to 25. The next identical proposal
            raises ``RepeatedCallError`` before the tool runs. Default 3.
        deadline_seconds: Optional wall-clock limit for the whole loop,
            checked before each planner call and before each tool call. It
            cannot interrupt a planner or tool call that is already running.
            Off by default. Raises ``AgentDeadlineExceeded``.
        clock: Monotonic clock, replaceable in tests.

    Side effects:
        None by default. Every call uses ``allow_writes=False`` regardless
        of whether the planner asks for a side-effecting tool.
    """

    def __init__(self, planner: Planner, tools: ToolRegistry, allowed_tools: set[str],
                 max_steps: int = 5, max_observation_chars: int = 3000,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None,
                 max_identical_calls: int = 3, deadline_seconds: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if not 1 <= max_steps <= 25 or not 100 <= max_observation_chars <= 20000:
            raise ValueError("Unsafe or invalid agent loop limits")
        if isinstance(max_identical_calls, bool) or not isinstance(max_identical_calls, int) \
                or not 1 <= max_identical_calls <= 25:
            raise ValueError("max_identical_calls must be an integer from 1 to 25")
        if deadline_seconds is not None and (isinstance(deadline_seconds, bool)
                or not isinstance(deadline_seconds, (int, float))
                or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 86400):
            raise ValueError("deadline_seconds must be greater than 0 and at most 86400")
        self.planner = planner
        self.tools = tools
        self.allowed_tools = frozenset(allowed_tools)
        self.max_steps = max_steps
        self.max_observation_chars = max_observation_chars
        self.on_event = on_event
        self.max_identical_calls = max_identical_calls
        self.deadline_seconds = deadline_seconds
        self.clock = clock

    def _stop(self, reason: str, steps: int, error: RuntimeError, name: str | None = None) -> NoReturn:
        """Record why the loop stopped, without arguments or output, then raise."""
        if self.on_event:
            payload: dict[str, Any] = {"reason": reason, "steps_run": steps}
            if name is not None:
                payload["name"] = name
            self.on_event("loop_stopped", payload)
        raise error

    def run(self, task: str, context: ContextCapsule) -> WorkerResult:
        """Execute a bounded loop and return observations without claiming verification.

        Raises:
            ToolPermissionDenied: The planner selected a disallowed tool.
            RepeatedCallError: The same call was proposed more than
                ``max_identical_calls`` times in a row. Subclass of RuntimeError.
            AgentDeadlineExceeded: ``deadline_seconds`` passed. Subclass of RuntimeError.
            RuntimeError: Maximum steps were exhausted without a Finish.
        """
        observations: list[str] = []
        started = self.clock()
        last_call: tuple[str, str] | None = None
        streak = 0

        def past_deadline() -> bool:
            return self.deadline_seconds is not None and self.clock() - started > self.deadline_seconds

        for step in range(self.max_steps):
            if past_deadline():
                self._stop("deadline", step, AgentDeadlineExceeded("Agent loop deadline exceeded"))
            action = self.planner.next_action(task, context, tuple(observations))
            if isinstance(action, Finish):
                return WorkerResult("prepared", action.message, tuple(observations))
            if not isinstance(action, ToolAction):
                raise TypeError("Planner output must be ToolAction or Finish")
            try:
                arguments_json = json.dumps(action.arguments, sort_keys=True)
            except (TypeError, ValueError):
                # Leave rejection of odd arguments to the tool registry, as before this check existed.
                arguments_json = repr(action.arguments)
            call = (action.name, arguments_json)
            streak = streak + 1 if call == last_call else 1
            last_call = call
            if streak > self.max_identical_calls:
                self._stop("repeated_call", step, RepeatedCallError(
                    f"Planner repeated the same tool call more than {self.max_identical_calls} times"), action.name)
            if past_deadline():
                self._stop("deadline", step, AgentDeadlineExceeded("Agent loop deadline exceeded"))
            if self.on_event:
                # Never store raw arguments or tool output in the global event ledger.
                self.on_event("tool_invocation", {"name": action.name,
                    "argument_keys": sorted(action.arguments),
                    "arguments_sha256": hashlib.sha256(arguments_json.encode()).hexdigest()})
            result = self.tools.call(action.name, action.arguments, set(self.allowed_tools), allow_writes=False)
            # The tool name comes first so a cut observation still shows which tool ran.
            rendered = '{"tool": ' + json.dumps(action.name) + ', "result": ' + json.dumps(result, sort_keys=True) + "}"
            observation = rendered[:self.max_observation_chars]
            observations.append(observation)
            if self.on_event:
                self.on_event("tool_observation", {"name": action.name, "size": len(observation),
                    "sha256": hashlib.sha256(observation.encode()).hexdigest(), "truncated": len(rendered) > len(observation)})
        self._stop("step_budget", self.max_steps, RuntimeError("Agent loop exhausted its tool-step budget"))


# Compared in lower case against the lower-cased path parts. A checkout on a filesystem that ignores
# case (macOS, Windows) serves ``Node_Modules`` for ``node_modules``.
_EXCLUDED = frozenset({".git", ".decisionbound", "node_modules", ".venv", "__pycache__"})
# Secret file names, compared in lower case with an exact match. A checkout on a filesystem that
# ignores case serves ``Credentials.json`` for ``credentials.json``, and a Linux repository may hold
# either spelling. Hidden names (a leading dot) are also refused as a whole, so the dotted names here
# keep the policy from depending on that rule.
_SECRET_NAMES = frozenset({
    ".env", ".envrc", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass", ".git-credentials", ".htpasswd",
    "credentials.json", "credentials.yaml", "credentials.yml", "credentials.txt",
    "secrets.json", "secrets.yaml", "secrets.yml", "secrets.txt",
    "secret.json", "secret.yaml", "secret.yml", "token.json",
    "kubeconfig", "kube-config", "kube_config",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
# Patterns matched with ``fnmatch.fnmatchcase`` on the lower-cased file name, never on the whole path.
# Each one is anchored at the start or the end of the name and none is a bare substring, so
# ``environment.md``, ``key_manager.py``, ``keys.md`` and ``secrets_policy.md`` stay readable. A key
# or certificate suffix also matches when another suffix follows, such as ``server.key.txt``, because
# the read tools serve ``.txt``, ``.md``, ``.json`` and ``.yaml`` files. The private key and
# environment file patterns follow the patch policy in ``code_patches.py``.
_SECRET_SUFFIXES = (".env", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".tfvars", ".tfstate")
_SECRET_PATTERNS = (
    tuple(pattern for suffix in _SECRET_SUFFIXES for pattern in ("*" + suffix, "*" + suffix + ".*"))
    + ("id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
       "*credentials.json", "*credentials.yaml", "*credentials.yml",
       "*secrets.json", "*secrets.yaml", "*secrets.yml", "client_secret*.json",
       "*service-account*.json", "*service_account*.json", "*serviceaccount*.json",
       "kubeconfig.*", "kube-config.*", "kube_config.*", "*.kubeconfig")
)
_SUPPORTED = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".ts", ".tsx", ".go", ".rs"}


def _is_secret_name(name: str) -> bool:
    """Tell whether a file name matches the secret name set or one of its patterns, in any case."""
    folded = name.casefold()
    return folded in _SECRET_NAMES or any(fnmatch.fnmatchcase(folded, pattern) for pattern in _SECRET_PATTERNS)


def _is_noncanonical(part: str) -> bool:
    """Tell whether a path part is a Windows alias or stream form of another name.

    Windows drops a trailing dot or space (``node_modules.`` opens ``node_modules``) and a colon starts an
    alternate data stream (``credentials.json:x``).
    """
    return part.endswith((".", " ")) or ":" in part


def _is_protected_path(parts: tuple[str, ...]) -> bool:
    """Tell whether a relative path is excluded, hidden, noncanonical or named like a secret file."""
    return (any(part.casefold() in _EXCLUDED or part.startswith(".") or _is_noncanonical(part) for part in parts)
            or _is_secret_name(parts[-1] if parts else ""))


def repository_readonly_tools(root: Path, max_file_bytes: int = 512_000) -> ToolRegistry:
    """Create safe local file inspection/search tools for a single project root.

    Both tools refuse excluded directories and hidden names, secret file names (see ``_SECRET_NAMES`` and
    ``_SECRET_PATTERNS``), noncanonical names (a trailing dot or space, or a colon, in any part) and
    symlinks. Names are compared without regard to case. Only a fixed set of text suffixes is served.

    This is a bounded inspection utility, not a secure sandbox against code
    executing with the same operating-system permissions as the process.
    """
    base = root.resolve(strict=True)
    registry = ToolRegistry()

    def safe_file(path: str) -> Path:
        """Deny protected names (see ``_is_protected_path``) and unsupported/binary file types.

        Every refusal names what failed and one next step. Messages never echo
        the caller's path and never carry a host path or an operating-system error.
        """
        candidate = Path(path)
        if candidate.is_absolute() or candidate.anchor:
            # Checked first so the answer does not depend on the host (``/etc`` is a symlink on macOS).
            raise ValueError("Paths must be relative to the project root and cannot contain '..'. "
                             "Call search_project to find valid file paths.")
        if (_is_protected_path(candidate.parts)
                or any((base / Path(*candidate.parts[:index])).is_symlink()
                       for index in range(1, len(candidate.parts) + 1))):
            raise PermissionError(
                "That path is protected and is never served. Hidden, secret-like, excluded and symlinked "
                "paths are refused. Call search_project to find readable source files.")
        if ".." in candidate.parts:
            raise ValueError("Paths must be relative to the project root and cannot contain '..'. "
                             "Call search_project to find valid file paths.")
        try:
            target = safe_relative_path(base, path)
            size = target.stat().st_size
        except ValueError as exc:
            raise ValueError("That path is not a regular file inside the project. "
                             "Call search_project to find valid file paths.") from exc
        except OSError as exc:
            raise FileNotFoundError("No such file in the project. "
                                    "Call search_project to find existing file paths.") from exc
        if target.suffix.lower() not in _SUPPORTED:
            raise PermissionError("Only text files with a supported extension are served (" +
                                  ", ".join(sorted(_SUPPORTED)) + "). Call search_project to find a "
                                  "supported file, or ask the operator to open this one locally.")
        if size > max_file_bytes:
            raise PermissionError(f"That file is larger than the {max_file_bytes} byte limit and is not served. "
                                  "Call search_project to find a smaller file, or ask the operator to open "
                                  "this one locally.")
        return target

    def read(args: dict) -> dict:
        """Return a capped excerpt of a reviewed local source file."""
        path = args.get("path")
        if not isinstance(path, str):
            raise ValueError("read_file needs a text path relative to the project root. "
                             "Call search_project to find file paths.")
        file = safe_file(path)
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # The operating-system text names the host path, so it is replaced by a fixed message.
            raise PermissionError("That file could not be read. Call search_project to find another file, "
                                  "or ask the operator to check its permissions.") from exc
        # At most 200 lines and 6000 chars are returned, independent of caller.
        return {"path": path, "text": "\n".join(text.splitlines()[:200])[:6000]}

    def search(args: dict) -> dict:
        """Bounded lexical search excluding hidden/secret and large files."""
        term = args.get("query")
        if not isinstance(term, str) or not 2 <= len(term) <= 120:
            raise ValueError("search_project needs a query of 2 to 120 characters. Call it again with a "
                             "shorter or longer term, for example a function or class name.")
        results = []
        count = 0
        inspected = 0
        for file in sorted(base.rglob("*")):
            inspected += 1
            if len(results) >= 10 or count >= 500 or inspected >= 2000:
                break
            if not file.is_file() or any(part.casefold() in _EXCLUDED for part in file.relative_to(base).parts):
                continue
            relative = str(file.relative_to(base))
            try:
                vetted = safe_file(relative)
            except (ValueError, PermissionError, FileNotFoundError):
                continue
            count += 1
            try:
                lines = vetted.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue  # An unreadable file is skipped like any other file the tool cannot serve.
            for number, line in enumerate(lines, start=1):
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
