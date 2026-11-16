#!/usr/bin/env python3
"""Generate an API reference directly from source definitions and docstrings.

Using Python AST avoids importing optional cloud/provider modules during a
static docs build. This script intentionally fails on undocumented public
classes and methods, so API changes cannot silently drift from documentation.
"""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "decisionbound"
TARGET = ROOT / "site_docs" / "api-reference.md"
MODULES = ("models", "ports", "storage", "identity", "profiles", "transfer",
           "context", "skills", "tools", "agent_loop", "model_planner", "providers", "execution", "mcp_server",
           "services", "runtime", "settings", "cli", "web",
           "code_models", "code_ports", "code_workspace", "code_patches",
           "code_sandbox", "code_execution", "code_provider", "code_artifacts",
           "code_acceptance", "code_journal", "code_repair", "code_native",
           "code_review", "supervised_coding", "workflows", "background", "review_gate", "client_sync", "local_auth", "onboarding", "state_integrity", "state_migration", "transferbench", "release_trust")


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Print a best-effort public signature without running annotations."""
    params = []
    args = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
    for arg, default in zip(args, defaults):
        if arg.arg == "self":
            continue
        s = arg.arg
        if arg.annotation:
            s += ": " + ast.unparse(arg.annotation)
        if default is not None:
            s += " = " + ast.unparse(default)
        params.append(s)
    if node.args.vararg:
        params.append("*" + node.args.vararg.arg)
    elif node.args.kwonlyargs:
        params.append("*")
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        s = arg.arg
        if arg.annotation:
            s += ": " + ast.unparse(arg.annotation)
        if default is not None:
            s += " = " + ast.unparse(default)
        params.append(s)
    if node.args.kwarg:
        params.append("**" + node.args.kwarg.arg)
    return_type = " -> " + ast.unparse(node.returns) if node.returns else ""
    return f"{node.name}({', '.join(params)}){return_type}"


def generate() -> tuple[int, int]:
    """Write the reference and return count of documented classes/functions."""
    lines = ["# Generated Python API reference", "",
             "> Generated from `src/decisionbound/*.py` docstrings by `scripts/generate_api_docs.py`.",
             "> API is alpha; versioned compatibility guarantees are not yet published.", ""]
    missing = []
    classes = functions = 0
    for module in MODULES:
        file = SOURCE / f"{module}.py"
        if not file.is_file():
            raise FileNotFoundError(file)
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        lines += [f"## `decisionbound.{module}`", "", ast.get_docstring(tree) or "", ""]
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                classes += 1
                doc = ast.get_docstring(node)
                if not doc:
                    missing.append(f"{module}.{node.name}")
                lines += [f"### `{node.name}`", "", doc or "*Undocumented.*", ""]
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_"):
                        functions += 1
                        description = ast.get_docstring(method)
                        if not description:
                            missing.append(f"{module}.{node.name}.{method.name}")
                        lines += [f"#### `{node.name}.{method.name}`", "", "```python", signature(method),
                                  "```", "", description or "*Undocumented.*", ""]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                functions += 1
                doc = ast.get_docstring(node)
                if not doc:
                    missing.append(f"{module}.{node.name}")
                lines += [f"### `{node.name}`", "", "```python", signature(node), "```", "",
                          doc or "*Undocumented.*", ""]
    if missing:
        raise SystemExit("Public APIs missing docstrings:\n" + "\n".join(missing))
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated API documentation: {classes} classes, {functions} methods/functions")
    return classes, functions


if __name__ == "__main__":
    generate()
