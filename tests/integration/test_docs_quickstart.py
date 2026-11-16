"""Keep the first five minutes honest: the documented quickstart must run as written.

The README and the Pages quickstart each carry one demo between
``<!-- quickstart:begin -->`` and ``<!-- quickstart:end -->`` markers. The first
``bash`` block is executed verbatim with the installed ``dbound`` console script,
and every ``json`` block (output pasted from a real run) must appear in the
captured output. Random identifiers are normalized on both sides.

Other documentation checks here are static and offline: every ``dbound`` command
and option written in a ``bash`` block exists in the real CLI, every ``pip install``
extra exists, and relative README links resolve.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import typer.main

from decisionbound.cli import app

ROOT = Path(__file__).resolve().parents[2]
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "site_docs").glob("*.md"))]
QUICKSTART_FILES = [ROOT / "README.md", ROOT / "site_docs" / "getting-started.md"]
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.S | re.M)
ID = re.compile(r"\b(dec|prj|run)_[0-9a-f]{32}\b")


def _blocks(text: str, language: str) -> list[str]:
    return [body for lang, body in FENCE.findall(text) if lang == language]


def _quickstart(path: Path) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    begin, end = "<!-- quickstart:begin -->", "<!-- quickstart:end -->"
    assert text.count(begin) == 1 and text.count(end) == 1, f"{path.name} needs one quickstart section"
    section = text.split(begin, 1)[1].split(end, 1)[0]
    scripts = _blocks(section, "bash")
    assert len(scripts) == 1, f"{path.name} quickstart must have exactly one bash block"
    expected = _blocks(section, "json")
    assert expected, f"{path.name} quickstart must show real output"
    return scripts[0], expected


def _normalize(text: str) -> str:
    return ID.sub(lambda m: m.group(1) + "_ID", text)


def _dbound_dir() -> Path:
    """Directory holding the installed ``dbound`` console script."""
    here = Path(sys.executable).parent
    if (here / "dbound").exists() or (here / "dbound.exe").exists():
        return here
    found = shutil.which("dbound")
    if not found:
        pytest.skip("installed dbound console script not found; run pip install first")
    return Path(found).parent


def _clean_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DBOUND_")}
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(_dbound_dir()), env.get("PATH", "")])
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    return env


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="quickstart is a bash script; Windows users run it from Git Bash or WSL")
@pytest.mark.parametrize("path", QUICKSTART_FILES, ids=lambda p: p.name)
def test_quickstart_runs_verbatim_and_matches_documented_output(path, tmp_path):
    script, expected = _quickstart(path)
    work = tmp_path / "work"
    work.mkdir()
    result = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=_clean_env(tmp_path),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = _normalize(result.stdout)
    position = 0
    for block in expected:
        wanted = _normalize(block.strip())
        found = actual.find(wanted, position)
        assert found >= 0, f"documented output not produced, in order, by the quickstart:\n{wanted}\n---actual---\n{actual}"
        position = found + len(wanted)
    # The demo must leave the real home directory alone.
    assert not (tmp_path / "home" / ".decisionbound").exists()


def test_quickstart_documents_the_three_transfer_outcomes_and_a_capsule():
    for path in QUICKSTART_FILES:
        _, expected = _quickstart(path)
        joined = "\n".join(expected)
        for action in ('"action": "apply"', '"action": "translate"', '"action": "abstain"'):
            assert action in joined, (path.name, action)
        assert '"items": [' in joined and "sha256=" in joined, path.name


def _walk(tokens: list[str]) -> list[str]:
    """Return problems for one ``dbound`` command line (empty when it is valid)."""
    # Duck typed: newer Typer vendors its own click classes.
    node = typer.main.get_command(app)
    seen = ["dbound"]
    rest = tokens[1:]
    while hasattr(node, "commands") and rest and not rest[0].startswith("-"):
        name = rest[0]
        if name not in node.commands:
            return [f"unknown command '{' '.join(seen)} {name}'"]
        node = node.commands[name]
        seen.append(name)
        rest = rest[1:]
    known = {"--help"}
    for param in node.params:
        known.update(param.opts)
        known.update(getattr(param, "secondary_opts", []))
    return [f"unknown option {tok.split('=')[0]} for '{' '.join(seen)}'"
            for tok in rest if tok.startswith("--") and tok.split("=")[0] not in known]


def _dbound_lines(text: str):
    for body in _blocks(text, "bash"):
        for line in re.sub(r"\\\n\s*", " ", body).splitlines():
            line = line.strip()
            if line.startswith("dbound"):
                yield line


@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: p.name)
def test_every_documented_dbound_command_and_option_exists(path):
    problems = []
    for line in _dbound_lines(path.read_text(encoding="utf-8")):
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError as exc:
            problems.append(f"{line!r}: {exc}")
            continue
        problems += [f"{line!r}: {p}" for p in _walk(tokens)]
    assert not problems, "\n".join(problems)


def test_command_walker_rejects_typos():
    assert _walk(shlex.split("dbound decision aprove x")) == ["unknown command 'dbound decision aprove'"]
    assert _walk(shlex.split("dbound prepare task --tokenz 5")) == ["unknown option --tokenz for 'dbound prepare'"]
    assert _walk(shlex.split("dbound decision approve ID --revision 1 --path .")) == []


def test_documented_pip_extras_exist():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    for path in DOC_FILES:
        for extras in re.findall(r"pip install[^\n]*?\.\[([\w,-]+)\]", path.read_text(encoding="utf-8")):
            for extra in extras.split(","):
                assert extra in declared, (path.name, extra)


def test_readme_links_resolve_and_state_status_honestly():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)\s]+)\)", text):
        assert (ROOT / target.split("#")[0]).exists(), f"README links to missing {target}"
    lowered = text.lower()
    assert "alpha" in lowered
    assert "benchmarks/carrybench/README.md" in text
    assert "no human" in lowered and "llm" in lowered, "README must say CarryBench labels are LLM-made"


def _example_env(tmp_path: Path) -> dict[str, str]:
    env = _clean_env(tmp_path)
    env["DBOUND_HOME"] = str(tmp_path / "state")
    return env


@pytest.mark.parametrize("script", ["custom_policy", "mock_readonly_agent", "mock_patch_agent", "mock_repair_agent"])
def test_readme_examples_run_offline(script, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "examples" / f"{script}.py")], cwd=ROOT,
                            env=_example_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("manifest", sorted((ROOT / "examples" / "workflow").glob("*.yaml")), ids=lambda p: p.name)
def test_example_workflow_manifests_validate(manifest, tmp_path):
    result = subprocess.run([str(_dbound_dir() / "dbound"), "workflow", "validate", "--file", str(manifest)],
                            cwd=ROOT, env=_example_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"valid": true' in result.stdout


def test_sdk_quickstart_snippet_runs(tmp_path):
    text = (ROOT / "site_docs" / "sdk.md").read_text(encoding="utf-8")
    begin, end = "<!-- sdk-quickstart:begin -->", "<!-- sdk-quickstart:end -->"
    assert text.count(begin) == 1 and text.count(end) == 1
    snippets = _blocks(text.split(begin, 1)[1].split(end, 1)[0], "python")
    assert len(snippets) == 1
    result = subprocess.run([sys.executable, "-I", "-c", snippets[0]], cwd=tmp_path,
                            env=_example_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Keep handlers thin" in result.stdout
