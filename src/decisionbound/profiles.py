"""User-facing provider/harness presets and safe, shareable custom profiles.

A profile is public configuration. Credentials are resolved separately from
environment variables or a secret manager; they are never embedded in exports.
"""
from __future__ import annotations
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
import yaml
from .storage import SQLiteRepository

BUILTINS: dict[str, dict[str, Any]] = {
    "local-safe": {"provider": "none", "harness": "offline", "model": None, "network": False},
    "local": {"provider": "ollama", "harness": "builtin-readonly", "model": "qwen2.5-coder", "network": False},
    "claude": {"provider": "anthropic", "harness": "external-claude", "model": None, "network": False},
    "codex": {"provider": "openai", "harness": "external-codex", "model": None, "network": False},
    "qwen": {"provider": "qwen", "harness": "builtin-readonly", "model": None, "network": False},
    "openrouter": {"provider": "openrouter", "harness": "builtin-readonly", "model": None, "network": False},
    "litellm": {"provider": "litellm", "harness": "builtin-readonly", "model": None, "network": False},
}
_REDACT_KEYS = re.compile(r"(secret|password|api.?key|access.?token|private.?key|credential)", re.I)
_ALLOWED = {"name", "extends", "provider", "harness", "model", "network", "context_max_tokens", "max_steps", "max_workers", "description", "revision"}


class InvalidProfile(ValueError):
    """Invalid, unsafe or unsupported user profile definition."""


def validate_profile(data: dict) -> dict:
    """Validate a shareable profile and reject embedded secrets.

    Unknown properties fail closed. This avoids future versions silently
    interpreting a formerly ignored field as an enabled capability.
    """
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        raise InvalidProfile("Profile needs a string name")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", data["name"]):
        raise InvalidProfile("Profile name must be lowercase and contain 2–64 safe characters")
    if set(data) - _ALLOWED:
        raise InvalidProfile(f"Unknown profile keys: {sorted(set(data) - _ALLOWED)}")
    if any(_REDACT_KEYS.search(k) for k in data):
        raise InvalidProfile("Secrets cannot be stored in profiles")
    if data.get("network") not in (None, False, True):
        raise InvalidProfile("network must be boolean")
    for key in ("context_max_tokens", "max_steps", "max_workers"):
        if key in data and (type(data[key]) is not int or data[key] < 1):
            raise InvalidProfile(f"{key} must be positive integer")
    if "revision" in data and (type(data["revision"]) is not int or data["revision"] < 1):
        raise InvalidProfile("Invalid profile revision")
    # Recursive check protects against a secret embedded in a nested string.
    for value in data.values():
        if isinstance(value, str) and re.search(r"(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_.-]{16,})", value):
            raise InvalidProfile("Profile appears to embed a credential")
        if isinstance(value, (dict, list)):
            raise InvalidProfile("Nested arbitrary profile values are unsupported in alpha")
    return dict(data)


class ProfileManager:
    """Resolve defaults and custom overlays through one durable registry.

    Default precedence: safe built-in -> custom overlay -> explicit invocation
    flags. Organization policy is a separate runtime boundary and cannot be
    weakened via profile import, even if a YAML file requests it.
    """

    def __init__(self, store: SQLiteRepository):
        self.store = store

    def names(self) -> list[str]:
        """List built-in and custom selectable names."""
        return sorted(set(BUILTINS) | {p["name"] for p in self.store.list_profiles()})

    def resolve(self, name: str) -> dict:
        """Return a validated, merged profile without secrets or side effects."""
        if name in BUILTINS:
            return {"name": name, **deepcopy(BUILTINS[name]), "revision": 0}
        custom = self.store.get_profile(name)
        if custom is None:
            raise KeyError(name)
        parent = custom.get("extends", "local-safe")
        if parent == name or parent not in BUILTINS:
            raise InvalidProfile("Custom profiles may extend one built-in profile")
        return {**BUILTINS[parent], **custom}

    def active(self) -> str:
        """Get the shared active profile or the safe offline default."""
        name = self.store.get_setting("active_profile", "local-safe")
        return name if name in self.names() else "local-safe"

    def use(self, name: str) -> dict:
        """Switch the profile for subsequent tasks; existing runs stay pinned."""
        profile = self.resolve(name)
        self.store.set_setting("active_profile", name)
        return profile

    def create(self, name: str, extends: str = "local-safe", **overrides: Any) -> dict:
        """Create a validated custom profile from a built-in template."""
        if extends not in BUILTINS:
            raise InvalidProfile("Custom profiles must extend a known built-in")
        return self.store.save_profile(validate_profile({"name": name, "extends": extends, **overrides}))

    def update(self, data: dict, expected_revision: int) -> dict:
        """Save a reviewed edit with optimistic concurrency control."""
        data = validate_profile(data)
        if data["name"] in BUILTINS:
            raise InvalidProfile("Built-ins are immutable; create a custom profile")
        return self.store.save_profile(data, expected_revision)

    def export(self, name: str, path: Path) -> None:
        """Write a credential-free YAML profile; reject symlink destinations."""
        if path.is_symlink():
            raise InvalidProfile("Refusing symlinked export target")
        profile = self.resolve(name)
        if name in BUILTINS:
            profile = {"name": f"my-{name}", "extends": name, "description": "Based on a built-in preset"}
        else:
            profile = self.store.get_profile(name)
        validated = validate_profile(profile)
        # no arbitrary profile keys; the store never accepts private secrets.
        with path.open("x", encoding="utf-8") as f:
            yaml.safe_dump(validated, f, sort_keys=False)

    def import_file(self, path: Path) -> dict:
        """Import YAML data only after schema and secret validation."""
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise InvalidProfile("Profile file is symlinked or too large")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data = validate_profile(data)
        data.pop("revision", None)
        if data["name"] in BUILTINS:
            raise InvalidProfile("Cannot overwrite a built-in profile")
        return self.store.save_profile(data)
