"""Safe end-user onboarding and deterministic runtime readiness diagnostics.

Setup is intentionally separate from execution: selecting a profile cannot
start a model, obtain a credential, turn on networking, or authorize a write.
Diagnostics disclose only capability presence, never credential values or
private paths. The module has no web/API dependency, so CLI and web clients
share the same decision logic and unit-test it without third-party services.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .identity import init_project, load_project
from .profiles import ProfileManager
from .storage import SQLiteRepository

# External model authentication is never interpreted as permission to connect.
_PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "qwen": "QWEN_API_KEY",
    "litellm": "LITELLM_API_KEY",
}
_HARNESS_COMMANDS = {"external-claude": "claude", "external-codex": "codex"}


@dataclass(frozen=True, slots=True)
class SetupCheck:
    """A credential-free, deterministic prerequisite check.

    ``ok`` is about local readiness, not successful provider authentication or
    certification of sandbox guarantees. ``required`` distinguishes blockers
    from optional tooling. Never add environment variable *values* to details.
    """

    key: str
    ok: bool
    required: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SetupReport:
    """End-user setup snapshot suitable for CLI JSON or authenticated HTTP.

    ``ready`` means the selected offline path can run without new prerequisites.
    For external configurations, it only means prerequisites are present; a
    real authenticated connection remains untested until the user opts in.
    """

    profile: str
    provider: str
    harness: str
    project_registered: bool | None
    ready: bool
    integration_tested: bool
    checks: tuple[SetupCheck, ...]
    next_action: str

    def to_dict(self) -> dict:
        """Return a serializable report without secrets or private filenames."""
        return asdict(self)


class SetupService:
    """Manage an end user's first-run selection and inspect prerequisites.

    This service can be replaced by a client UI, but should remain the final
    policy boundary for profile selection and project registration. A configured
    profile never grants model, network, tool or worktree write consent.

    Args:
        store: Shared SQLite registry for project identity and active profile.
        environ: Injectable environment for tests; only key presence is read.
        which: Injectable executable resolver; no executable is invoked.
    """

    def __init__(self, store: SQLiteRepository,
                 environ: Mapping[str, str] | None = None,
                 which: Callable[[str], str | None] | None = None):
        self.store = store
        self.profiles = ProfileManager(store)
        self.environ = os.environ if environ is None else environ
        self.which = shutil.which if which is None else which

    def inspect(self, profile: str | None = None, project_path: Path | None = None) -> SetupReport:
        """Inspect one profile without creating files or using network services.

        Missing project identity is reported rather than repaired implicitly.
        This allows ``dbound doctor`` and a setup screen to be side-effect-free.
        """
        resolved = self.profiles.resolve(profile or self.profiles.active())
        name = resolved["name"]
        provider = resolved["provider"]
        harness = resolved["harness"]
        checks: list[SetupCheck] = []
        registered = None
        if project_path is not None:
            try:
                load_project(project_path, self.store)
                registered = True
            except (ValueError, KeyError, FileNotFoundError, PermissionError, OSError):
                registered = False
            checks.append(SetupCheck("project", registered, True,
                                     "Project identity verified" if registered else
                                     "Initialize this repository explicitly with dbound init"))

        if harness in _HARNESS_COMMANDS:
            command = _HARNESS_COMMANDS[harness]
            present = bool(self.which(command))
            checks.append(SetupCheck("native_cli", present, True,
                                     f"{command} executable detected" if present else
                                     f"Install and authenticate the {command} CLI"))
        elif provider == "ollama":
            present = bool(self.which("ollama"))
            checks.append(SetupCheck("ollama_cli", present, True,
                                     "Ollama executable detected; model/service not tested" if present else
                                     "Install and start Ollama; select a local model"))
        elif provider in _PROVIDER_KEYS:
            env_key = _PROVIDER_KEYS[provider]
            present = bool(self.environ.get(env_key))
            checks.append(SetupCheck("provider_credential", present, True,
                                     f"{env_key} is configured (not validated)" if present else
                                     f"Set {env_key} in your environment or secret manager"))
        elif provider != "none":
            checks.append(SetupCheck("provider", False, True,
                                     "Unsupported provider; select a supported profile"))

        if provider != "none":
            checks.append(SetupCheck("network_consent", False, False,
                                     "External access requires explicit authorization per run"))
        checks.append(SetupCheck("write_consent", False, False,
                                 "Code modification always requires separate explicit consent"))
        ready = all(check.ok for check in checks if check.required)
        if not ready:
            action = next(check.detail for check in checks if check.required and not check.ok)
        elif provider == "none":
            action = "Run dbound prepare or dbound run; no credentials required"
        else:
            action = "Opt in to a bounded integration test; setup did not contact a provider"
        return SetupReport(name, provider, harness, registered, ready, False,
                           tuple(checks), action)

    def configure(self, profile: str, project_path: Path | None = None,
                  initialize_project: bool = False) -> SetupReport:
        """Select a profile and optionally register a requested repository.

        Invalid profile names fail before any repository initialization. This
        intentionally does not accept secrets, expose keys, or test models.
        A project is never initialized unless ``initialize_project`` is true.
        """
        self.profiles.resolve(profile)
        if initialize_project and project_path is None:
            raise ValueError("Explicit project path required when initializing")
        if initialize_project:
            assert project_path is not None
            manifest = project_path / ".decisionbound" / "project.yaml"
            if manifest.exists() or manifest.is_symlink():
                # Never register a forged, copied, or corrupt manifest merely
                # because its path exists. Existing identities must verify.
                load_project(project_path, self.store)
            else:
                init_project(project_path, self.store)
        self.profiles.use(profile)
        return self.inspect(profile, project_path)
