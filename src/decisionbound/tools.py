"""Strict-schema, least-privilege registry for tools used by read-only agents."""
from __future__ import annotations
from dataclasses import dataclass, field
from collections.abc import Callable, Mapping
from typing import Any


class ToolPermissionDenied(PermissionError):
    """An agent attempted to call a tool without explicit authorization."""


class ToolSchemaError(ValueError):
    """A tool's argument shape or value type failed strict validation."""


@dataclass(frozen=True)
class RegisteredTool:
    """A tool implementation and its strict, inspectable argument contract.

    Args:
        name: Unique tool identifier.
        description: Human-readable purpose, not authorization policy.
        handler: Host-side function that receives already validated arguments.
        side_effects: True if the tool can change state outside the agent.
        arg_types: Exact required field names and Python scalar types. Extra
            fields, missing values, and incorrect types always fail closed.
    """
    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    side_effects: bool = False
    arg_types: Mapping[str, type] = field(default_factory=dict)


class ToolRegistry:
    """Allowlisted strict-schema tool broker, independent of prompt content.

    Unknown tools and write-capable tools fail closed by default. Tool
    descriptions are filtered before disclosure to keep context smaller.
    """
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register a unique, schema-checked tool without granting permission."""
        if tool.name in self._tools or not tool.name or not tool.description or not callable(tool.handler):
            raise ValueError("Duplicate or invalid tool")
        if any(t not in (str, int, bool, float) for t in tool.arg_types.values()):
            raise ToolSchemaError("Only strict scalar argument types are supported in alpha")
        self._tools[tool.name] = tool

    def describe(self, allowed: set[str]) -> list[dict[str, Any]]:
        """Return only allowlisted schemas and metadata, not every registry tool."""
        return [{"name": t.name, "description": t.description, "side_effects": t.side_effects,
                 "parameters": {k: v.__name__ for k, v in t.arg_types.items()}}
                for t in self._tools.values() if t.name in allowed]

    def call(self, name: str, args: dict[str, Any], allowed: set[str], allow_writes: bool = False) -> dict[str, Any]:
        """Validate exact argument schema and host permissions before invoking.

        Raises:
            ToolPermissionDenied: Tool name or effect is not authorized.
            ToolSchemaError: Arguments are missing, extra or incorrectly typed.
        """
        if name not in self._tools or name not in allowed:
            raise ToolPermissionDenied(f"Tool not permitted: {name}")
        tool = self._tools[name]
        if tool.side_effects and not allow_writes:
            raise ToolPermissionDenied(f"Write permission not granted: {name}")
        if not isinstance(args, dict):
            raise ToolSchemaError("Tool arguments must be a dictionary")
        if set(args) != set(tool.arg_types):
            raise ToolSchemaError(f"Expected exactly: {sorted(tool.arg_types)}")
        for key, expected in tool.arg_types.items():
            if type(args[key]) is not expected:
                raise ToolSchemaError(f"{key} must be exactly {expected.__name__}")
        output = tool.handler(args)
        if not isinstance(output, dict):
            raise ToolSchemaError("Tool handler must return a dictionary")
        return output
