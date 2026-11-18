"""The line audit of the mined CarryBench config-fact pools.

Each rule is tested with small invented files that reproduce one real failure
shape (a first match on the wrong line) and with the correct line, so the tests
fail if a rule is loosened. No third-party text is used.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "carrybench"
TOOL = BENCH / "real_v1" / "tools" / "audit_pool_lines.py"
spec = importlib.util.spec_from_file_location("audit_pool_lines", TOOL)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_pool_lines"] = audit
spec.loader.exec_module(audit)

POOLS = [BENCH / "real_v1" / "config_facts_v1.jsonl",
         BENCH / "real_v2_holdout" / "config_facts_v2_holdout.jsonl"]
REPORT = BENCH / "pool_line_audit_v1.json"


def row(how, family, member, line, rid="r1", repo="o/r", path="x"):
    return {"id": rid, "kind": "config_fact", "how": how, "line_start": line, "repo": repo,
            "sha": "a" * 40, "path": path, "families": [{"family": family, "member": member}]}


def number(text, needle, nth=1):
    hits = [i for i, line in enumerate(text.splitlines(), 1) if needle in line]
    return hits[nth - 1]


PKG = """{
  "name": "demo",
  "repository": {
    "type": "git",
    "url": "x"
  },
  "scripts": {
    "format": "oxfmt",
    "test": "vitest"
  },
  "prettier": "@scope/config",
  "peerDependencies": {
    "eslint": "^9"
  },
  "devDependencies": {
    "oxfmt": "1.0.0",
    "vitest": "2.0.0"
  },
  "type": "module",
  "packageManager": "pnpm@9.0.0+sha512.abc"
}
"""


def test_a_type_inside_repository_is_not_the_module_type():
    wrong = audit.audit_row(row("package_type_field", "js_module_system", "module", number(PKG, '"type": "git"')), PKG)
    assert wrong["suggested_line"] == number(PKG, '"type": "module"')
    assert audit.audit_row(row("package_type_field", "js_module_system", "module", number(PKG, '"type": "module"')), PKG) is None


def test_a_nested_type_with_the_same_value_is_still_not_the_top_level_type():
    text = """{
  "name": "demo",
  "exports": {
    "type": "module"
  },
  "type": "module"
}
"""
    nested = number(text, '"type": "module"', 1)
    result = audit.audit_row(row("package_type_field", "js_module_system", "module", nested), text)
    assert result["suggested_line"] == number(text, '"type": "module"', 2)


def test_the_type_value_must_match_the_recorded_member():
    line = number(PKG, '"type": "module"')
    result = audit.audit_row(row("package_type_field", "js_module_system", "commonjs", line), PKG)
    assert result and result["suggested_line"] == 0


def test_a_script_that_runs_a_tool_is_not_the_dependency_declaration():
    wrong = audit.audit_row(row("dependency_declared", "js_formatter", "oxfmt", number(PKG, '"format": "oxfmt"')), PKG)
    assert wrong["suggested_line"] == number(PKG, '"oxfmt": "1.0.0"')
    assert audit.audit_row(row("dependency_declared", "js_formatter", "oxfmt", number(PKG, '"oxfmt": "1.0.0"')), PKG) is None


def test_a_top_level_tool_config_key_is_not_the_dependency_declaration():
    result = audit.audit_row(row("dependency_declared", "js_formatter", "prettier", number(PKG, '"prettier"')), PKG)
    assert result and "dependencies" in result["why"]


def test_a_peer_dependency_is_not_a_declaration_the_miner_reads():
    result = audit.audit_row(row("dependency_declared", "js_linter", "eslint", number(PKG, '"eslint"')), PKG)
    assert result and result["suggested_line"] == 0  # eslint is declared only as a peer here


def test_package_manager_must_be_top_level_and_name_the_member():
    line = number(PKG, '"packageManager"')
    assert audit.audit_row(row("packageManager_field", "js_package_manager", "pnpm", line), PKG) is None
    assert audit.audit_row(row("packageManager_field", "js_package_manager", "yarn", line), PKG)


def test_a_minified_package_json_is_reported_as_unauditable_not_as_wrong():
    result = audit.audit_row(row("package_type_field", "js_module_system", "module", 1),
                             '{"name":"a","type":"module"}\n')
    assert result["unauditable"] is True


def test_a_line_past_the_end_of_the_file_is_wrong():
    result = audit.audit_row(row("package_type_field", "js_module_system", "module", 99), PKG)
    assert "outside" in result["why"]


TOML = '''[project]
name = "demo"
requires-python = ">=3.10"

# The uv settings live under [tool.uv] further down and [tool.ruff] sets lint.
[tool.black]
line-length = 110

[tool.uvicorn]
workers = 2

[tool.ruff]
line-length = 110
note = """
[tool.pytest]
"""

[tool.ruff.lint]
select = ["D"]  # conventions below

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.uv.sources]
a = { path = "a" }

[tool.other]
requires-python = ">=3.8"
'''


def test_a_comment_that_names_a_table_is_not_the_table_header():
    comment = number(TOML, "# The uv settings")
    result = audit.audit_row(row("pyproject_tool_table", "py_env_manager", "uv", comment), TOML)
    assert result["suggested_line"] == number(TOML, "[tool.uv.sources]")
    assert audit.audit_row(row("pyproject_tool_table", "py_env_manager", "uv", number(TOML, "[tool.uv.sources]")), TOML) is None


def test_a_table_with_a_longer_name_is_not_accepted_for_a_shorter_tool():
    result = audit.audit_row(row("pyproject_tool_table", "py_env_manager", "uv", number(TOML, "[tool.uvicorn]")), TOML)
    assert result and result["suggested_line"] == number(TOML, "[tool.uv.sources]")


def test_text_inside_a_multi_line_string_is_not_a_header():
    inside = number(TOML, "[tool.pytest]")
    assert audit.audit_row(row("pyproject_tool_table", "py_test_runner", "pytest", inside), TOML)


def test_a_line_length_under_the_other_tool_is_wrong_even_when_the_number_matches():
    black_line = number(TOML, "line-length", 1)
    ruff_line = number(TOML, "line-length", 2)
    result = audit.audit_row(row("ruff_line_length", "py_line_length", "110", black_line), TOML)
    assert result["suggested_line"] == ruff_line
    assert audit.audit_row(row("ruff_line_length", "py_line_length", "110", ruff_line), TOML) is None
    assert audit.audit_row(row("black_line_length", "py_line_length", "110", black_line), TOML) is None
    assert audit.audit_row(row("black_line_length", "py_line_length", "110", ruff_line), TOML)["suggested_line"] == black_line


def test_a_line_length_value_that_differs_from_the_member_is_wrong():
    assert audit.audit_row(row("ruff_line_length", "py_line_length", "88", number(TOML, "line-length", 2)), TOML)


def test_a_comment_that_mentions_conventions_is_not_the_pydocstyle_convention():
    comment = number(TOML, "# conventions below")
    result = audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "google", comment), TOML)
    assert result["suggested_line"] == number(TOML, 'convention = "google"')
    real = number(TOML, 'convention = "google"')
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "google", real), TOML) is None
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "numpy", real), TOML)


def test_requires_python_must_sit_under_project_and_match_the_member():
    good = number(TOML, 'requires-python = ">=3.10"')
    other = number(TOML, 'requires-python = ">=3.8"')
    assert audit.audit_row(row("requires_python", "py_min_version", ">=3.10", good), TOML) is None
    wrong_table = audit.audit_row(row("requires_python", "py_min_version", ">=3.10", other), TOML)
    assert wrong_table["suggested_line"] == good
    assert audit.audit_row(row("requires_python", "py_min_version", ">=3.12", good), TOML)


def test_a_comment_with_triple_quotes_does_not_hide_the_headers_after_it():
    text = '# strings like """ are explained here\n[tool.ruff]\nline-length = 90\n'
    assert audit.audit_row(row("ruff_line_length", "py_line_length", "90", 3), text) is None


def test_the_same_requires_python_under_another_table_is_not_the_project_setting():
    text = '[tool.pdm]\nrequires-python = ">=3.10"\n\n[project]\nrequires-python = ">=3.10"\n'
    result = audit.audit_row(row("requires_python", "py_min_version", ">=3.10", 2), text)
    assert result["suggested_line"] == 5
    assert audit.audit_row(row("requires_python", "py_min_version", ">=3.10", 5), text) is None


def test_an_unknown_method_is_reported_and_never_passes_silently():
    result = audit.audit_row(row("made_up_method", "f", "m", 1), TOML)
    assert "no audit rule" in result["why"]


def test_every_method_in_the_frozen_pools_has_a_rule():
    for pool in POOLS:
        for raw in pool.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                if record["line_start"] > 0:
                    assert record["how"] in {
                        "package_type_field", "packageManager_field", "dependency_declared", "pyproject_tool_table",
                        "requires_python", "ruff_line_length", "black_line_length", "ruff_pydocstyle_convention"}


def test_end_to_end_report_counts_each_outcome_and_strict_mode_exits_nonzero(tmp_path):
    files = tmp_path / "files"
    (files / "o__r").mkdir(parents=True)
    (files / "o__r" / "package.json").write_text(PKG, encoding="utf-8")
    pool = tmp_path / "pool.jsonl"
    rows = [row("package_type_field", "js_module_system", "module", number(PKG, '"type": "module"'), "good", path="package.json"),
            row("package_type_field", "js_module_system", "module", number(PKG, '"type": "git"'), "bad", path="package.json"),
            row("package_type_field", "js_module_system", "module", 3, "gone", repo="o/missing", path="package.json"),
            dict(row("package_type_field", "js_module_system", "module", 0, "nolines", path="package.json"))]
    pool.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    report = audit.audit_pools(files, [pool])
    assert (report["checked"], report["ok"]) == (2, 1)
    assert [m["id"] for m in report["mismatches"]] == ["bad"]
    assert [m["id"] for m in report["missing_source"]] == ["gone"]
    assert report["by_how"]["package_type_field"] == {"checked": 2, "mismatch": 1}
    out = tmp_path / "out.json"
    argv = ["--clones-dir", str(files), "--pool", str(pool), "--out", str(out)]
    assert audit.main(argv) == 0
    assert audit.main(argv + ["--strict"]) == 1
    assert json.loads(out.read_text())["format"] == "carrybench-pool-line-audit-v1"


# ----------------------------------------------- the committed audit of the real pools

def _report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _pool_rows():
    rows = {}
    for pool in POOLS:
        for raw in pool.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                rows[(pool.name, record["id"])] = record
    return rows


def test_committed_report_adds_up_and_matches_the_pool_rows():
    report, rows = _report(), _pool_rows()
    line_bearing = [r for r in rows.values() if r["line_start"] > 0 and r["kind"] == "config_fact"]
    assert report["checked"] + len(report["unauditable"]) + len(report["missing_source"]) == len(line_bearing)
    assert report["ok"] + len(report["mismatches"]) == report["checked"]
    assert not report["missing_source"]
    for m in report["mismatches"]:
        record = rows[(m["pool"], m["id"])]
        assert (record["repo"], record["sha"], record["path"], record["how"]) == (m["repo"], m["sha"], m["path"], m["how"])
        assert record["line_start"] == m["recorded_line"] != m["suggested_line"]


def test_the_audit_agrees_with_the_erratum_and_no_other_case_pointer_is_wrong():
    """Case evidence keeps repo and sha one level above the pointer, so both are read together."""
    flagged = {(m["repo"], m["sha"], m["path"], m["recorded_line"]) for m in _report()["mismatches"]}
    erratum = set()
    for raw in (BENCH / "real_v2_holdout" / "errata_v2_holdout_v1.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(raw)
        if record.get("status") == "wrong_location":
            erratum.add((record["repo"], record["sha"], record["path"], record["recorded"]["line_start"]))
    used = set()
    case_files = list((BENCH / "real_v1").glob("*.jsonl")) + list((BENCH / "real_v2_holdout").glob("*.jsonl"))
    for path in case_files:
        if "config_facts" in path.name or "errata" in path.name:
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            target = json.loads(raw).get("target")
            for ev in (target or {}).get("evidence", []) if isinstance(target, dict) else []:
                if ev.get("line_start", 0) > 0 and str(ev.get("kind", "")).startswith("config_fact"):
                    key = (target["repo"], target["sha"], ev["path"], ev["line_start"])
                    if key in flagged:
                        used.add(key)
    # Every pointer the erratum found wrong by reading is flagged by the audit, and the audit
    # flags no other pointer that a case uses (the eight the erratum confirmed are not flagged).
    assert used == erratum
    assert len(used) == 2


@pytest.mark.skipif(not os.environ.get("CARRYBENCH_FILES"),
                    reason="needs the pinned files, set CARRYBENCH_FILES to a directory laid out as owner__repo/path")
def test_committed_report_is_what_the_tool_computes_from_the_pinned_files():
    fresh = audit.audit_pools(Path(os.environ["CARRYBENCH_FILES"]), POOLS)
    assert fresh == _report()
