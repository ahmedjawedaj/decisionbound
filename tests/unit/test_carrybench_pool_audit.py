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


# ------------------------------------------------ cases that kill surviving mutants

def test_a_nested_package_manager_key_is_not_the_top_level_one():
    text = '{\n  "name": "a",\n  "engines": {\n    "packageManager": "pnpm@9.0.0"\n  },\n  "packageManager": "pnpm@9.0.0"\n}\n'
    nested = audit.audit_row(row("packageManager_field", "js_package_manager", "pnpm", 4), text)
    assert nested and nested["suggested_line"] == 6
    assert audit.audit_row(row("packageManager_field", "js_package_manager", "pnpm", 6), text) is None


def test_a_dependency_line_for_another_tool_is_not_this_tools_declaration():
    vitest = number(PKG, '"vitest": "2.0.0"')
    result = audit.audit_row(row("dependency_declared", "js_formatter", "oxfmt", vitest), PKG)
    assert result and result["suggested_line"] == number(PKG, '"oxfmt": "1.0.0"')


def test_a_convention_under_another_table_is_not_the_pydocstyle_convention():
    text = '[tool.other]\nconvention = "google"\n\n[tool.ruff.lint.pydocstyle]\nconvention = "google"\n'
    result = audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "google", 2), text)
    assert result and result["suggested_line"] == 5
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "google", 5), text) is None


def test_the_older_pydocstyle_table_location_is_also_accepted():
    text = '[tool.ruff.pydocstyle]\nconvention = "numpy"\n'
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "numpy", 2), text) is None


def test_pep257_is_recorded_as_sphinx_the_way_the_miner_records_it():
    text = '[tool.ruff.lint.pydocstyle]\nconvention = "pep257"\n'
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "sphinx", 2), text) is None
    assert audit.audit_row(row("ruff_pydocstyle_convention", "py_docstring_style", "pep257", 2), text)


def test_the_ruff_format_table_is_not_the_plain_ruff_table():
    text = '[tool.ruff]\nline-length = 90\n\n[tool.ruff.format]\nquote-style = "double"\n'
    result = audit.audit_row(row("pyproject_tool_table", "py_formatter", "ruff_format", 1), text)
    assert result and result["suggested_line"] == 4
    assert audit.audit_row(row("pyproject_tool_table", "py_formatter", "ruff_format", 4), text) is None


def test_two_triple_quote_marks_on_one_line_do_not_open_a_string():
    text = 'note = """one line"""\n[tool.ruff]\nline-length = 90\n'
    assert audit.audit_row(row("ruff_line_length", "py_line_length", "90", 3), text) is None


def _pool_file(tmp_path, rows):
    pool = tmp_path / "pool.jsonl"
    pool.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return pool


def _folder(tmp_path):
    files = tmp_path / "files"
    (files / "o__r").mkdir(parents=True)
    (files / "o__r" / "package.json").write_text(PKG, encoding="utf-8")
    return files


def test_strict_mode_also_fails_when_only_a_source_is_missing_or_unauditable(tmp_path):
    files = _folder(tmp_path)
    missing = _pool_file(tmp_path, [row("package_type_field", "js_module_system", "module", 3, "gone",
                                        repo="o/absent", path="package.json")])
    argv = ["--clones-dir", str(files), "--pool", str(missing), "--out", str(tmp_path / "o.json")]
    assert audit.main(argv) == 0 and audit.main(argv + ["--strict"]) == 1
    (files / "o__r" / "min.json").write_text('{"type":"module"}\n', encoding="utf-8")
    flat = _pool_file(tmp_path, [row("package_type_field", "js_module_system", "module", 1, "flat", path="min.json")])
    argv = ["--clones-dir", str(files), "--pool", str(flat), "--out", str(tmp_path / "o.json")]
    assert audit.main(argv) == 0 and audit.main(argv + ["--strict"]) == 1


def test_a_row_with_no_family_is_unauditable_and_does_not_crash():
    bare = dict(row("package_type_field", "x", "y", 1))
    bare["families"] = []
    result = audit.audit_row(bare, PKG)
    assert result["unauditable"] is True


def test_the_same_id_in_two_pools_counts_twice_and_within_one_pool_once(tmp_path):
    files = _folder(tmp_path)
    good = row("package_type_field", "js_module_system", "module", number(PKG, '"type": "module"'), "same", path="package.json")
    first = _pool_file(tmp_path, [good, good])
    second = tmp_path / "second.jsonl"
    second.write_text(json.dumps(good) + "\n", encoding="utf-8")
    assert audit.audit_pools(files, [first])["checked"] == 1
    assert audit.audit_pools(files, [first, second])["checked"] == 2


@pytest.mark.parametrize("path", ["../escape.json", "/etc/passwd", "a/../../b", "a\\b", ""])
def test_a_row_that_names_a_path_outside_its_folder_stops_the_run(tmp_path, path):
    files = _folder(tmp_path)
    with pytest.raises(ValueError, match="unsafe repository path"):
        audit._load(files, "o/r", "a" * 40, path)


def test_a_malformed_commit_or_repository_name_stops_the_run(tmp_path):
    files = _folder(tmp_path)
    with pytest.raises(ValueError, match="commit"):
        audit._load(files, "o/r", "--output=x", "package.json")
    for bad in ("o/..", "o/../r", "../r", "onlyone", "o/"):
        with pytest.raises(ValueError, match="repository name"):
            audit._load(files, bad, "a" * 40, "package.json")


def test_a_symlink_in_the_path_of_a_row_stops_the_run(tmp_path):
    files = _folder(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    (files / "o__r" / "link.json").symlink_to(secret)
    (files / "o__r" / "dir").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        audit._load(files, "o/r", "a" * 40, "link.json")
    with pytest.raises(ValueError, match="symlink"):
        audit._load(files, "o/r", "a" * 40, "dir/secret.txt")
    assert "demo" in audit._load(files, "o/r", "a" * 40, "package.json")


def test_list_files_prints_each_file_the_audit_reads_once(tmp_path, capsys):
    good = row("package_type_field", "js_module_system", "module", 3, "a", path="package.json")
    again = row("package_type_field", "js_module_system", "module", 9, "b", path="package.json")
    nolines = row("package_type_field", "js_module_system", "module", 0, "c", path="other.json")
    pool = _pool_file(tmp_path, [good, again, nolines])
    assert audit.main(["--list-files", "--pool", str(pool)]) == 0
    assert capsys.readouterr().out == f"o/r\t{'a' * 40}\tpackage.json\n"
    real = audit.list_files(POOLS)
    assert len(real) == len(set(real)) == 126


def test_case_pointers_that_the_audit_covers_and_the_one_checked_by_hand():
    """24 distinct config-fact pointers are used by case files. 23 are pool rows the audit covers.
    The 24th (tbr-n39, PrefectHQ/prefect ui-v2/package.json line 145) is not a pool row."""
    pool_keys = {(r["repo"], r["sha"], r["path"], r["line_start"])
                 for r in _pool_rows().values() if r["line_start"] > 0 and r["kind"] == "config_fact"}
    used = {}
    for path in list((BENCH / "real_v1").glob("*.jsonl")) + list((BENCH / "real_v2_holdout").glob("*.jsonl")):
        if "config_facts" in path.name or "errata" in path.name:
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                case = json.loads(raw)
                target = case.get("target")
                for ev in (target.get("evidence", []) if isinstance(target, dict) else []):
                    if ev.get("line_start", 0) > 0 and str(ev.get("kind", "")).startswith("config_fact"):
                        used.setdefault((target["repo"], target["sha"], ev["path"], ev["line_start"]), set()).add(case.get("case_id"))
    assert len(used) == 24
    outside = {k: v for k, v in used.items() if k not in pool_keys}
    assert len(used) - len(outside) == 23
    assert list(outside.values()) == [{"tbr-n39"}]
    (repo, _, path, line), = outside
    assert (repo, path, line) == ("PrefectHQ/prefect", "ui-v2/package.json", 145)


def test_a_commit_with_a_trailing_newline_is_refused(tmp_path):
    files = _folder(tmp_path)
    with pytest.raises(ValueError, match="commit"):
        audit._load(files, "o/r", "a" * 40 + "\n", "package.json")


@pytest.mark.parametrize("path", ["a\nb.json", "a\tb.json", "a\rb.json", "a\0b.json"])
def test_control_characters_in_a_path_are_refused(tmp_path, path):
    with pytest.raises(ValueError, match="unsafe repository path"):
        audit._load(_folder(tmp_path), "o/r", "a" * 40, path)


def test_list_files_refuses_a_row_that_could_forge_a_line_of_output(tmp_path):
    forged = row("package_type_field", "js_module_system", "module", 3, "x", path="a.json\no/r\tbbbb\tb.json")
    with pytest.raises(ValueError, match="unsafe repository path"):
        audit.list_files([_pool_file(tmp_path, [forged])])


def test_a_key_line_inside_a_tools_table_is_not_its_header():
    text = '[tool.uv]\nindex-strategy = "x"\n'
    result = audit.audit_row(row("pyproject_tool_table", "py_env_manager", "uv", 2), text)
    assert result and result["suggested_line"] == 1


def test_a_minified_file_is_counted_as_unauditable_and_not_as_wrong(tmp_path):
    files = _folder(tmp_path)
    (files / "o__r" / "min.json").write_text('{"type":"module"}\n', encoding="utf-8")
    pool = _pool_file(tmp_path, [row("package_type_field", "js_module_system", "module", 1, "flat", path="min.json")])
    report = audit.audit_pools(files, [pool])
    assert [u["id"] for u in report["unauditable"]] == ["flat"]
    assert report["mismatches"] == [] and report["checked"] == 0


def test_a_multi_line_string_with_several_inner_lines_hides_all_of_them():
    text = 'note = """\n[tool.uv]\n[tool.uv.sources]\n"""\n[tool.uv.workspace]\n'
    result = audit.audit_row(row("pyproject_tool_table", "py_env_manager", "uv", 2), text)
    assert result and result["suggested_line"] == 5


def test_a_compact_two_line_json_is_unauditable():
    text = '{"name": "a",\n"type": "module"}\n'
    assert audit.audit_row(row("package_type_field", "js_module_system", "module", 2), text)["unauditable"] is True


def test_a_git_clone_is_read_from_the_pinned_commit_and_not_the_working_tree(tmp_path):
    import subprocess
    clone = tmp_path / "files" / "o__r"
    clone.mkdir(parents=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@e", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    run = lambda *a: subprocess.run(["git", "-C", str(clone), *a], check=True, capture_output=True, text=True, env=env)
    run("init", "-q")
    (clone / "package.json").write_text(PKG, encoding="utf-8")
    run("add", "package.json")
    run("commit", "-q", "-m", "c")
    sha = run("rev-parse", "HEAD").stdout.strip()
    (clone / "package.json").write_text('{"changed": true}\n', encoding="utf-8")
    assert '"name": "demo"' in audit._load(tmp_path / "files", "o/r", sha, "package.json")
    assert audit._load(tmp_path / "files", "o/r", sha, "missing.json") is None
