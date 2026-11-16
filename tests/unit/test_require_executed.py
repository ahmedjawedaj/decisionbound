"""Tests for the connected-gate guard that stops silent skips from passing CI."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("dbound_require_executed", ROOT / "scripts" / "require_executed.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()

DOCKER = "tests/integration/test_docker_real_optional.py"
SUPERVISED = "tests/integration/test_supervised_docker_real_optional.py"


def _xml(path: Path, cases: str) -> Path:
    path.write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
                    + cases + '</testsuite></testsuites>', encoding="utf-8")
    return path


def _passed(module: str, name: str = "test_it") -> str:
    return f'<testcase classname="{module}" name="{name}" time="0.1"/>'


def test_all_required_files_executed_and_passed_is_accepted(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional")
               + _passed("tests.integration.test_supervised_docker_real_optional"))
    assert guard.check_junit([xml], [DOCKER, SUPERVISED]) == []


def test_zero_tests_for_a_named_file_is_rejected(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional"))
    problems = guard.check_junit([xml], [DOCKER, SUPERVISED])
    assert len(problems) == 1
    assert SUPERVISED in problems[0] and "zero tests" in problems[0]


def test_skipped_test_is_rejected_and_reason_is_shown(tmp_path):
    xml = _xml(tmp_path / "r.xml",
               '<testcase classname="tests.integration.test_docker_real_optional" name="test_real">'
               '<skipped type="pytest.skip" message="Docker smoke requires a pre-pulled image"/></testcase>')
    problems = guard.check_junit([xml], [DOCKER])
    assert len(problems) == 1
    assert "SKIPPED" in problems[0] and "pre-pulled image" in problems[0]


def test_module_level_skip_is_attributed_to_its_file(tmp_path):
    # pytest reports allow_module_level skips with an empty classname and the module as the name.
    xml = _xml(tmp_path / "r.xml",
               '<testcase classname="" name="tests.integration.test_browser_live_sprint10_optional">'
               '<skipped message="collection skipped">Requires connected host</skipped></testcase>')
    problems = guard.check_junit([xml], ["tests/integration/test_browser_live_sprint10_optional.py"])
    assert any("SKIPPED" in p for p in problems)
    assert not any("zero tests" in p for p in problems)


def test_failure_and_error_are_rejected(tmp_path):
    xml = _xml(tmp_path / "r.xml",
               '<testcase classname="tests.integration.test_docker_real_optional" name="a"><failure message="boom"/></testcase>'
               '<testcase classname="tests.integration.test_supervised_docker_real_optional" name="b"><error message="bad"/></testcase>')
    problems = guard.check_junit([xml], [DOCKER, SUPERVISED])
    assert any("FAILED" in p and "boom" in p for p in problems)
    assert any("ERROR" in p and "bad" in p for p in problems)


def test_one_skip_among_passes_still_fails(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional", "a")
               + '<testcase classname="tests.integration.test_docker_real_optional" name="b"><skipped message="x"/></testcase>')
    assert guard.check_junit([xml], [DOCKER])


def test_a_similarly_named_file_does_not_satisfy_a_requirement(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional_extra"))
    problems = guard.check_junit([xml], [DOCKER])
    assert problems and "zero tests" in problems[0]


def test_class_based_tests_are_attributed_to_their_module(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional.TestReal"))
    assert guard.check_junit([xml], [DOCKER]) == []


def test_results_from_several_xml_files_are_combined(tmp_path):
    first = _xml(tmp_path / "a.xml", _passed("tests.integration.test_docker_real_optional"))
    second = _xml(tmp_path / "b.xml", _passed("tests.integration.test_supervised_docker_real_optional"))
    assert guard.check_junit([first, second], [DOCKER, SUPERVISED]) == []


def test_missing_or_malformed_xml_is_rejected(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuites><oops", encoding="utf-8")
    assert guard.check_junit([tmp_path / "absent.xml"], [DOCKER])
    assert any("parse" in p.lower() for p in guard.check_junit([bad], [DOCKER]))


def test_empty_requirement_list_is_a_usage_error(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional"))
    assert guard.main(["junit", "--require", "", str(xml)]) == 2


def test_cli_exit_codes_and_message(tmp_path, capsys):
    good = _xml(tmp_path / "good.xml", _passed("tests.integration.test_docker_real_optional"))
    assert guard.main(["junit", "--require", DOCKER, str(good)]) == 0
    bad = _xml(tmp_path / "bad.xml", '<testcase classname="tests.integration.test_docker_real_optional" name="t"><skipped message="no docker"/></testcase>')
    assert guard.main(["junit", "--require", DOCKER, str(bad)]) == 1
    err = capsys.readouterr().err
    assert "test_docker_real_optional" in err and "no docker" in err


def test_real_pytest_run_with_module_skip_fails_the_guard(tmp_path):
    """End to end: a genuine pytest JUnit file for a silently skipping module is rejected."""
    sample = tmp_path / "test_silent_skip_optional.py"
    sample.write_text(textwrap.dedent("""
        import pytest
        pytest.skip('opt-in not enabled', allow_module_level=True)
        def test_never_runs():
            assert True
    """), encoding="utf-8")
    xml = tmp_path / "junit.xml"
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
                             str(sample), f"--junitxml={xml}"], cwd=tmp_path, capture_output=True, text=True)
    import xml.etree.ElementTree as ET
    suite = next(ET.parse(xml).getroot().iter("testsuite"))
    assert suite.get("skipped") == "1" and suite.get("failures") == "0" and suite.get("errors") == "0"
    problems = guard.check_junit([xml], ["test_silent_skip_optional.py"])
    assert problems and any("SKIPPED" in p for p in problems)


def test_real_pytest_run_that_executes_passes_the_guard(tmp_path):
    sample = tmp_path / "test_runs_optional.py"
    sample.write_text("def test_runs():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    xml = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
                    str(sample), f"--junitxml={xml}"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert guard.check_junit([xml], ["test_runs_optional.py"]) == []


# ---- log marker mode (real VS Code host runs emit no JUnit file) ----

def test_log_markers_all_present(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("Loading development extension at /x\nExtension host test runner exit code: 0\n", encoding="utf-8")
    assert guard.check_log(log, ["Loading development extension", "Extension host test runner exit code: 0"]) == []


def test_log_marker_missing_is_rejected(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("nothing useful\n", encoding="utf-8")
    problems = guard.check_log(log, ["Extension host test runner exit code: 0"])
    assert len(problems) == 1 and "exit code: 0" in problems[0]


def test_log_missing_file_is_rejected(tmp_path):
    assert guard.check_log(tmp_path / "absent.log", ["x"])


def test_log_exit_code_nonzero_does_not_match_zero_marker(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("Extension host test runner exit code: 1\n", encoding="utf-8")
    assert guard.check_log(log, ["Extension host test runner exit code: 0"])


# ---- certification report mode ----

def _report(path: Path, **overrides) -> Path:
    data = {"checks_passed": ["a", "real_uvicorn_node_extension_transport"], "real_http_node": True}
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_report_with_required_check_and_flag_is_accepted(tmp_path):
    report = _report(tmp_path / "r.json")
    assert guard.check_report(report, ["real_uvicorn_node_extension_transport"], ["real_http_node"]) == []


def test_report_missing_check_or_false_flag_is_rejected(tmp_path):
    report = _report(tmp_path / "r.json", checks_passed=["a"], real_http_node=False)
    problems = guard.check_report(report, ["real_uvicorn_node_extension_transport"], ["real_http_node"])
    assert len(problems) == 2


def test_report_failed_certification_or_bad_json_is_rejected(tmp_path):
    assert guard.check_report(_report(tmp_path / "r.json", certification="failed"), [], [])
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert guard.check_report(broken, [], [])
    assert guard.check_report(tmp_path / "absent.json", [], [])


def test_report_with_utf16_bom_from_powershell_is_read(tmp_path):
    path = tmp_path / "r.json"
    path.write_bytes(json.dumps({"checks_passed": ["x"], "real_http_node": True}).encode("utf-16"))
    assert guard.check_report(path, ["x"], ["real_http_node"]) == []


# ---- VSIX packaging mode ----

def _vsix(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


ENTRY = "const vscode = require('vscode');\nexports.activate = function activate(context) { return { refresh() {} }; };\n"

GOOD_VSIX = {"extension.vsixmanifest": "<PackageManifest/>", "[Content_Types].xml": "<Types/>",
             "extension/package.json": json.dumps({"name": "x", "publisher": "p", "version": "1.2.3",
                                                    "main": "./src/extension.cjs"}),
             "extension/src/extension.cjs": ENTRY}


def test_vsix_with_manifest_and_entry_is_accepted(tmp_path):
    assert guard.check_vsix(_vsix(tmp_path / "a.vsix", GOOD_VSIX)) == []


def test_vsix_missing_file_or_not_a_zip_is_rejected(tmp_path):
    assert guard.check_vsix(tmp_path / "absent.vsix")
    fake = tmp_path / "fake.vsix"
    fake.write_text("not a zip", encoding="utf-8")
    assert guard.check_vsix(fake)


def test_vsix_missing_entry_point_or_manifest_is_rejected(tmp_path):
    no_entry = {k: v for k, v in GOOD_VSIX.items() if k != "extension/src/extension.cjs"}
    assert any("entry" in p for p in guard.check_vsix(_vsix(tmp_path / "a.vsix", no_entry)))
    no_manifest = {k: v for k, v in GOOD_VSIX.items() if k != "extension.vsixmanifest"}
    assert guard.check_vsix(_vsix(tmp_path / "b.vsix", no_manifest))


def test_vsix_empty_entry_point_is_rejected(tmp_path):
    empty = dict(GOOD_VSIX, **{"extension/src/extension.cjs": ""})
    assert guard.check_vsix(_vsix(tmp_path / "a.vsix", empty))


def test_cli_vsix_mode(tmp_path):
    assert guard.main(["vsix", str(_vsix(tmp_path / "a.vsix", GOOD_VSIX))]) == 0
    assert guard.main(["vsix", str(tmp_path / "absent.vsix")]) == 1


@pytest.mark.parametrize("argv", [[], ["junit"], ["log"], ["report"], ["vsix"], ["bogus"]])
def test_cli_usage_errors_exit_two(argv):
    assert guard.main(argv) == 2


# ---- review round: stricter modes ----

def test_unlisted_failing_test_in_the_results_is_rejected(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional")
               + '<testcase classname="tests.other.test_stray" name="t"><skipped message="stray skip"/></testcase>')
    problems = guard.check_junit([xml], [DOCKER])
    assert len(problems) == 1 and "unlisted" in problems[0] and "stray skip" in problems[0]


def test_xfail_counts_as_not_executed(tmp_path):
    xml = _xml(tmp_path / "r.xml", '<testcase classname="tests.integration.test_docker_real_optional" name="t">'
               '<skipped type="pytest.xfail" message="expected failure"/></testcase>')
    problems = guard.check_junit([xml], [DOCKER])
    assert problems and "SKIPPED" in problems[0]


def test_require_must_name_a_python_file(tmp_path, capsys):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional"))
    for bad in ("tests/integration", "tests/integration/test_docker_real_optional", "x.txt=1", "a.py=0", "a.py=x", "=2"):
        assert guard.main(["junit", "--require", bad, str(xml)]) == 2, bad
    assert "usage error" in capsys.readouterr().err


def test_minimum_test_count_per_file(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional", "a")
               + _passed("tests.integration.test_docker_real_optional", "b"))
    assert guard.main(["junit", "--require", DOCKER + "=2", str(xml)]) == 0
    assert guard.main(["junit", "--require", DOCKER + "=3", str(xml)]) == 1


def test_minimum_total_count_across_files(tmp_path):
    xml = _xml(tmp_path / "r.xml", _passed("tests.integration.test_docker_real_optional")
               + _passed("tests.integration.test_supervised_docker_real_optional"))
    base = ["junit", "--require", DOCKER, "--require", SUPERVISED]
    assert guard.main(base + ["--min-total", "2", str(xml)]) == 0
    assert guard.main(base + ["--min-total", "3", str(xml)]) == 1
    assert guard.main(base + ["--min-total", "0", str(xml)]) == 2


def test_parse_requirement():
    assert guard.parse_requirement("a/b.py") == ("a/b.py", 1)
    assert guard.parse_requirement("a/b.py=4") == ("a/b.py", 4)
    with pytest.raises(ValueError):
        guard.parse_requirement("a/b")


def test_log_marker_does_not_match_a_longer_number(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("Extension host test runner exit code: 01\n", encoding="utf-8")
    assert guard.check_log(log, ["Extension host test runner exit code: 0"])


def test_log_marker_ending_in_equals_matches_a_following_number(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("DBOUND_HOST_SMOKE_OK commands=5\n", encoding="utf-8")
    assert guard.check_log(log, ["DBOUND_HOST_SMOKE_OK commands="]) == []
    assert guard.check_log(log, ["DBOUND_HOST_SMOKE_OK commands=5"]) == []
    assert guard.check_log(log, ["DBOUND_HOST_SMOKE_OK commands=6"])


FORBID = ["Extension host test runner exit code: [1-9]", "VS Code host smoke failed"]


def test_log_forbidden_patterns_are_rejected_even_when_markers_exist(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("Loading development extension at /x\nExtension host test runner exit code: 0\n"
                   "VS Code host smoke failed: boom\n", encoding="utf-8")
    problems = guard.check_log(log, ["Loading development extension at"], FORBID)
    assert len(problems) == 1 and "smoke failed" in problems[0]
    log.write_text("Extension host test runner exit code: 1\n", encoding="utf-8")
    assert guard.check_log(log, [], FORBID)
    log.write_text("Extension host test runner exit code: 0\n", encoding="utf-8")
    assert guard.check_log(log, [], FORBID) == []


def test_log_forbid_invalid_regex_is_a_usage_error(tmp_path):
    log = tmp_path / "host.log"
    log.write_text("x\n", encoding="utf-8")
    assert guard.main(["log", "--marker", "x", "--forbid", "(", str(log)]) == 2


def test_main_success_paths_for_log_and_report(tmp_path, capsys):
    log = tmp_path / "host.log"
    log.write_text("Loading development extension at /x\nExtension host test runner exit code: 0\n", encoding="utf-8")
    assert guard.main(["log", "--marker", "Loading development extension at",
                       "--forbid", FORBID[0], str(log)]) == 0
    assert "OK" in capsys.readouterr().out
    report = _report(tmp_path / "r.json", package_source="installed-distribution")
    assert guard.main(["report", "--check", "real_uvicorn_node_extension_transport", "--flag", "real_http_node",
                       "--expect", "package_source=installed-distribution", str(report)]) == 0
    assert "OK" in capsys.readouterr().out
    assert guard.main(["report", "--check", "missing_check", str(report)]) == 1


def test_report_must_be_a_json_object_starting_the_file(tmp_path):
    arr = tmp_path / "a.json"
    arr.write_text("[1, 2]", encoding="utf-8")
    assert any("object" in p for p in guard.check_report(arr, [], []))
    lead = tmp_path / "lead.json"
    lead.write_text("warning text\n" + json.dumps({"checks_passed": ["x"]}), encoding="utf-8")
    assert any("start" in p for p in guard.check_report(lead, ["x"], []))


def test_report_any_certification_value_is_rejected(tmp_path):
    # certify_platform.py prints no certification key on success, only {"certification": "failed", ...} on failure.
    for value in ("failed", "passed", "ok", ""):
        assert guard.check_report(_report(tmp_path / "r.json", certification=value), [], []), value


def test_report_non_empty_checks_failed_is_rejected(tmp_path):
    assert guard.check_report(_report(tmp_path / "r.json", checks_failed=["boom"]), [], [])
    assert guard.check_report(_report(tmp_path / "r.json", checks_failed=[]), [], []) == []


def test_report_expect_value_mismatch_is_rejected(tmp_path):
    report = _report(tmp_path / "r.json", package_source="editable")
    problems = guard.check_report(report, [], [], ["package_source=installed-distribution"])
    assert len(problems) == 1 and "package_source" in problems[0]
    assert guard.check_report(tmp_path / "r.json", [], [], ["package_source"])  # malformed expectation


def _source(path: Path, **overrides) -> Path:
    data = {"name": "x", "publisher": "p", "version": "1.2.3"}
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_vsix_identity_must_match_source_package(tmp_path):
    vsix = _vsix(tmp_path / "a.vsix", GOOD_VSIX)
    assert guard.check_vsix(vsix, _source(tmp_path / "ok.json")) == []
    for field, value in (("name", "other"), ("publisher", "q"), ("version", "9.9.9")):
        problems = guard.check_vsix(vsix, _source(tmp_path / "bad.json", **{field: value}))
        assert len(problems) == 1 and field in problems[0], field
    assert guard.check_vsix(vsix, tmp_path / "absent.json")


def test_vsix_manifest_must_be_an_object(tmp_path):
    members = dict(GOOD_VSIX, **{"extension/package.json": "[1]"})
    assert any("object" in p for p in guard.check_vsix(_vsix(tmp_path / "a.vsix", members)))


def test_vsix_requires_content_types_and_non_empty_manifest(tmp_path):
    no_types = {k: v for k, v in GOOD_VSIX.items() if k != "[Content_Types].xml"}
    assert any("Content_Types" in p for p in guard.check_vsix(_vsix(tmp_path / "a.vsix", no_types)))
    blank = dict(GOOD_VSIX, **{"extension.vsixmanifest": "  \n"})
    assert any("vsixmanifest" in p for p in guard.check_vsix(_vsix(tmp_path / "b.vsix", blank)))


@pytest.mark.parametrize("entry", ["", "x", "// only a comment about the extension entry point and nothing else\n",
                                   "/* block comment that is long enough to look like content but is not */",
                                   "module.exports = {};"])
def test_vsix_entry_must_contain_real_code(tmp_path, entry):
    members = dict(GOOD_VSIX, **{"extension/src/extension.cjs": entry})
    assert any("entry" in p for p in guard.check_vsix(_vsix(tmp_path / "a.vsix", members)))


def test_cli_vsix_mode_with_source_package(tmp_path):
    vsix = _vsix(tmp_path / "a.vsix", GOOD_VSIX)
    assert guard.main(["vsix", "--source-package", str(_source(tmp_path / "s.json")), str(vsix)]) == 0
    assert guard.main(["vsix", "--source-package", str(_source(tmp_path / "s.json", version="2")), str(vsix)]) == 1


# ---- node:test summary guard (the vscode-extension job) -------------------------------
def _node_log(path: Path, *, tests=13, passed=None, fail=0, cancelled=0, skipped=0, todo=0, marker="#") -> Path:
    passed = tests - fail - cancelled - skipped - todo if passed is None else passed
    lines = [f"{marker} {k} {v}" for k, v in (("tests", tests), ("suites", 0), ("pass", passed), ("fail", fail),
                                              ("cancelled", cancelled), ("skipped", skipped), ("todo", todo))]
    path.write_text("TAP version 13\nok 1 - a\n1..%d\n" % tests + "\n".join(lines) + "\n# duration_ms 188.0\n",
                    encoding="utf-8")
    return path


def test_node_tests_all_passing_meets_the_minimum(tmp_path):
    log = _node_log(tmp_path / "n.log")
    assert guard.check_node_tests(log, 13) == []
    assert guard.main(["node", "--min-tests", "13", str(log)]) == 0


@pytest.mark.parametrize("kwargs, word", [
    (dict(skipped=1), "skipped"), (dict(todo=1), "todo"), (dict(fail=1), "fail"), (dict(cancelled=2), "cancelled")])
def test_node_tests_skips_todos_failures_and_cancellations_are_rejected(tmp_path, kwargs, word):
    problems = guard.check_node_tests(_node_log(tmp_path / "n.log", **kwargs), 1)
    assert any(word in p for p in problems), problems


def test_node_tests_below_the_minimum_or_empty_run_is_rejected(tmp_path):
    assert guard.check_node_tests(_node_log(tmp_path / "n.log", tests=12), 13)
    assert guard.check_node_tests(_node_log(tmp_path / "z.log", tests=0), 1)


def test_node_tests_missing_summary_or_missing_log_is_rejected(tmp_path):
    empty = tmp_path / "e.log"
    empty.write_text("npm warn something\n", encoding="utf-8")
    assert any("summary" in p for p in guard.check_node_tests(empty, 1))
    assert guard.check_node_tests(tmp_path / "absent.log", 1)


def test_node_tests_passed_count_must_equal_tests_run(tmp_path):
    assert guard.check_node_tests(_node_log(tmp_path / "n.log", tests=13, passed=12), 1)


def test_node_tests_spec_reporter_summary_and_ansi_colour_are_understood(tmp_path):
    log = _node_log(tmp_path / "n.log", marker="ℹ")
    assert guard.check_node_tests(log, 13) == []
    coloured = tmp_path / "c.log"
    coloured.write_text("\x1b[34mℹ tests 13\x1b[39m\n\x1b[34mℹ pass 13\x1b[39m\nℹ fail 0\n"
                        "ℹ cancelled 0\nℹ skipped 0\nℹ todo 0\n", encoding="utf-8")
    assert guard.check_node_tests(coloured, 13) == []


def test_node_min_tests_must_be_positive(tmp_path):
    log = _node_log(tmp_path / "n.log")
    assert guard.main(["node", "--min-tests", "0", str(log)]) == 2
    assert guard.main(["node", str(log)]) == 2


def test_ci_vscode_extension_job_runs_the_node_guard():
    """The job must capture npm test output and fail when the guard rejects it."""
    import yaml
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    steps = jobs["vscode-extension"]["steps"]
    text = "\n".join(str(step.get("run", "")) for step in steps)
    assert "npm test" in text and "require_executed.py node" in text and "--min-tests" in text
    assert "pipefail" in text or any(step.get("shell") == "bash" for step in steps), \
        "a piped npm test must not hide its exit status"
