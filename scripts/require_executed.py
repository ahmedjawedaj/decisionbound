#!/usr/bin/env python3
"""Fail closed when a connected CI gate passes without really executing its tests.

Optional integration tests skip silently when Docker, Chromium, the MCP SDK or an
opt-in environment variable is missing, and a skipped test still leaves a green
job. This guard turns that into a failure. It is standard library only.

Modes:
  junit   Read pytest JUnit XML. Every named test file (FILE.py or FILE.py=N for
          a minimum of N tests) must have produced tests, and no test may be
          skipped, xfailed, failed or errored. --min-total N pins the overall count.
  log     Require literal markers in a captured log and reject forbidden regexes.
          Used for the real VS Code Extension Development Host run, which prints
          no JUnit file.
  node    Read captured `node --test` output (TAP or spec reporter). The summary must
          be present, at least --min-tests tests must have run, every one must have
          passed, and the fail, cancelled, skipped and todo counts must all be zero.
  vsix    Require the packaged VSIX to be a real zip with a non-empty archive
          manifest, [Content_Types].xml, a package.json object whose identity
          matches --source-package, and an entry point holding real code.
  report  Validate the JSON printed by scripts/certify_platform.py: it must
          start with a JSON object, carry no "certification" key (only failures
          print one), have an empty checks_failed, contain the required checks,
          true flags and expected values.

Exit codes: 0 all good, 1 guard violation, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _module_name(path: str) -> str:
    """Turn tests/integration/test_x.py into the dotted name pytest uses in JUnit."""
    text = path.strip().replace("\\", "/")
    if text.endswith(".py"):
        text = text[:-3]
    return text.strip("/").replace("/", ".")


def parse_requirement(value: str) -> tuple[str, int]:
    """Parse FILE.py or FILE.py=N into (file, minimum test count)."""
    text = value.strip()
    path, sep, count = text.rpartition("=") if "=" in text else (text, "", "")
    minimum = 1
    if sep:
        if not count.isdigit() or int(count) < 1:
            raise ValueError(f"minimum test count must be a positive integer: {value!r}")
        minimum = int(count)
    else:
        path = text
    if not path.endswith(".py") or path.rstrip("/").split("/")[-1] == ".py":
        raise ValueError(f"--require must name a Python test file ending in .py: {value!r}")
    return path, minimum


def _matches(module: str, classname: str, name: str) -> bool:
    """True when a testcase belongs to the module (class tests and module-level skips included)."""
    if classname:
        return classname == module or classname.startswith(module + ".")
    return name == module or name.startswith(module + ".")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def _status(case: ET.Element) -> tuple[str, str]:
    for tag, label in (("error", "ERROR"), ("failure", "FAILED"), ("skipped", "SKIPPED")):
        node = case.find(tag)
        if node is not None:
            message = (node.get("message") or "").strip()
            body = (node.text or "").strip()
            reason = message if (not body or body in message or message in body) else (f"{message}: {body}" if message else body)
            return label, reason.replace("\n", " ")[:240]
    return "PASSED", ""


def check_junit(xml_paths, required_files, min_total: int = 0) -> list[str]:
    """Return a list of human readable violations (empty means the gate really ran).

    Each required entry is a path or a (path, minimum) pair.
    """
    requirements = [parse_requirement(item) if isinstance(item, str) else item for item in required_files]
    required_files = [path for path, _ in requirements]
    problems: list[str] = []
    flat: list[tuple[str, str, ET.Element]] = []
    for xml_path in xml_paths:
        path = Path(xml_path)
        if not path.is_file():
            problems.append(f"JUnit file not found: {path} (the test step did not write results)")
            continue
        try:
            root = ET.fromstring(path.read_bytes())
        except ET.ParseError as error:
            problems.append(f"Cannot parse JUnit file {path}: {error}")
            continue
        flat.extend((case.get("classname") or "", case.get("name") or "", case)
                    for case in root.iter("testcase"))
    executed = 0
    for required, minimum in requirements:
        module = _module_name(required)
        mine = [item for item in flat if _matches(module, item[0], item[1])]
        if not mine:
            problems.append(f"{required}: produced zero tests (collected nothing or never ran)")
            continue
        if len(mine) < minimum:
            problems.append(f"{required}: produced {len(mine)} test(s), expected at least {minimum}")
        for classname, name, element in mine:
            label, reason = _status(element)
            if label != "PASSED":
                ident = f"{classname}::{name}" if classname else name
                detail = f" - {reason}" if reason else ""
                problems.append(f"{required}: {ident} {label}{detail}")
            else:
                executed += 1
    # Any skip, failure or error anywhere in the supplied results is also a violation.
    required_modules = [_module_name(item) for item in required_files]
    for classname, name, element in flat:
        label, reason = _status(element)
        if label != "PASSED" and not any(_matches(m, classname, name) for m in required_modules):
            ident = f"{classname}::{name}" if classname else name
            detail = f" - {reason}" if reason else ""
            problems.append(f"unlisted test {ident} {label}{detail}")
    if min_total and executed < min_total:
        problems.append(f"only {executed} test(s) passed across the required files, expected at least {min_total}")
    return problems


def check_log(log_path, markers, forbid=()) -> list[str]:
    """Require each literal marker in the log and reject every forbidden regex.

    A marker that ends in a word character must not be a prefix of a longer token,
    so "exit code: 0" does not match "exit code: 01".
    """
    path = Path(log_path)
    if not path.is_file():
        return [f"Log file not found: {path} (the step did not capture output)"]
    text = _read_text(path)
    problems = []
    for marker in markers:
        tail = r"(?!\w)" if re.match(r"\w", marker[-1:] or " ") else ""
        if not re.search(re.escape(marker) + tail, text):
            problems.append(f"Required evidence missing from {path}: {marker!r}")
    for pattern in forbid:
        found = re.search(pattern, text)
        if found:
            problems.append(f"Forbidden output present in {path}: {found.group(0)!r} (pattern {pattern!r})")
    return problems


def check_report(report_path, required_checks, required_flags, expect=()) -> list[str]:
    """Validate a certify_platform.py JSON report."""
    path = Path(report_path)
    if not path.is_file():
        return [f"Certification report not found: {path}"]
    try:
        text = _read_text(path).strip()
        if not text.startswith("{"):
            return [f"Certification report {path} must start with a JSON object, found {text[:20]!r}"]
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as error:
        return [f"Cannot parse certification report {path}: {error}"]
    if not isinstance(data, dict):
        return [f"Certification report {path} is not a JSON object"]
    problems = []
    # certify_platform.py prints a "certification" key only on failure, never on success.
    if "certification" in data:
        problems.append(f"Certification report carries a certification value: {data.get('certification')!r}"
                        f" ({data.get('reason', 'no reason')})")
    if data.get("checks_failed"):
        problems.append(f"Certification reported failed checks: {data.get('checks_failed')!r}")
    passed = data.get("checks_passed")
    passed = passed if isinstance(passed, list) else []
    for check in required_checks:
        if check not in passed:
            problems.append(f"Required certification check did not run: {check}")
    for flag in required_flags:
        if data.get(flag) is not True:
            problems.append(f"Required certification flag is not true: {flag}")
    for item in expect:
        key, sep, value = item.partition("=")
        if not sep or not key:
            problems.append(f"Malformed expectation {item!r}, use KEY=VALUE")
        elif data.get(key) != value:
            problems.append(f"Certification field {key} is {data.get(key)!r}, expected {value!r}")
    return problems


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_NODE_SUMMARY = re.compile(r"^\s*(?:#|\u2139)\s+(tests|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$", re.M)


def check_node_tests(log_path, min_tests: int) -> list[str]:
    """Verify a captured ``node --test`` run really executed tests and skipped none.

    ``node --test`` exits 0 when every test is skipped or marked todo, and when a
    glob selects few files, so the exit status alone cannot prove the job ran its
    tests. Both the TAP summary (``# pass 13``) and the spec reporter summary
    (``\u2139 pass 13``) are read, with colour codes removed. The last summary wins.
    """
    path = Path(log_path)
    if not path.is_file():
        return [f"Node test log not found: {path} (the test step did not capture output)"]
    counts: dict[str, int] = {}
    for key, value in _NODE_SUMMARY.findall(_ANSI.sub("", _read_text(path))):
        counts[key] = int(value)
    missing = [key for key in ("tests", "pass", "fail", "cancelled", "skipped", "todo") if key not in counts]
    if missing:
        return [f"No node:test summary in {path}: missing {', '.join(missing)} (tests did not run to the end)"]
    problems = []
    if counts["tests"] < min_tests:
        problems.append(f"node:test ran {counts['tests']} test(s), expected at least {min_tests}")
    for key in ("fail", "cancelled", "skipped", "todo"):
        if counts[key]:
            problems.append(f"node:test reported {counts[key]} {key} test(s)")
    if counts["pass"] != counts["tests"]:
        problems.append(f"node:test passed {counts['pass']} of {counts['tests']} test(s)")
    return problems


MIN_ENTRY_CODE_CHARS = 32
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code_characters(source: str) -> int:
    """Count non-whitespace characters left after stripping JavaScript comments."""
    return len(re.sub(r"\s+", "", _COMMENTS.sub("", source)))


def check_vsix(vsix_path, source_package=None) -> list[str]:
    """Verify a VSIX archive is real, carries real code and matches the source package.

    The entry point must hold at least MIN_ENTRY_CODE_CHARS characters of code once
    comments and whitespace are removed: that rejects empty files, comment only files
    and one-line stubs such as "module.exports = {};" (18 characters), while the
    real extension entry is several kilobytes.
    """
    path = Path(vsix_path)
    if not path.is_file():
        return [f"VSIX not found: {path} (packaging did not produce an artifact)"]
    expected = None
    if source_package is not None:
        try:
            expected = json.loads(_read_text(Path(source_package)))
            if not isinstance(expected, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as error:
            return [f"Cannot read source package {source_package}: {error}"]
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            problems = [f"VSIX lacks {required}" for required in
                        ("extension.vsixmanifest", "[Content_Types].xml", "extension/package.json")
                        if required not in names]
            if "extension.vsixmanifest" in names and not archive.read("extension.vsixmanifest").strip():
                problems.append("VSIX extension.vsixmanifest is empty")
            if "extension/package.json" in names:
                manifest = json.loads(archive.read("extension/package.json").decode("utf-8-sig"))
                if not isinstance(manifest, dict):
                    return problems + ["VSIX extension/package.json is not a JSON object"]
                if expected is not None:
                    for field in ("name", "publisher", "version"):
                        if manifest.get(field) != expected.get(field):
                            problems.append(f"VSIX {field} {manifest.get(field)!r} does not match source package "
                                            f"{expected.get(field)!r}")
                main = str(manifest.get("main", "")).replace("\\", "/")
                entry = "extension/" + main[2:] if main.startswith("./") else "extension/" + main
                if not main or entry not in names:
                    problems.append(f"VSIX lacks its declared entry point {main!r}")
                else:
                    code = _code_characters(archive.read(entry).decode("utf-8", errors="replace"))
                    if code < MIN_ENTRY_CODE_CHARS:
                        problems.append(f"VSIX entry point {main!r} holds only {code} characters of code "
                                        f"(minimum {MIN_ENTRY_CODE_CHARS} after removing comments)")
            return problems
    except (zipfile.BadZipFile, ValueError) as error:
        return [f"VSIX {path} is not a readable archive: {error}"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    junit = sub.add_parser("junit", help="verify named test files executed and passed")
    junit.add_argument("--require", action="append", default=[], metavar="TEST_FILE[=N]",
                       help="test .py file that must produce at least N (default 1) passing tests (repeatable)")
    junit.add_argument("--min-total", type=int, default=None, metavar="N",
                       help="minimum passing tests across all required files")
    junit.add_argument("xml", nargs="+", help="pytest JUnit XML file(s)")
    log = sub.add_parser("log", help="verify literal markers appear in a captured log")
    log.add_argument("--marker", action="append", default=[], help="literal text that must appear in the log (repeatable)")
    log.add_argument("--forbid", action="append", default=[], metavar="REGEX",
                     help="regular expression that must NOT match anywhere in the log (repeatable)")
    log.add_argument("logfile")
    node = sub.add_parser("node", help="verify captured `node --test` output ran tests and skipped none")
    node.add_argument("--min-tests", type=int, default=None, metavar="N",
                      help="minimum number of tests that must have run and passed")
    node.add_argument("logfile")
    vsix = sub.add_parser("vsix", help="verify a packaged VSIX archive")
    vsix.add_argument("--source-package", metavar="PACKAGE_JSON",
                      help="source package.json whose name, publisher and version the VSIX must carry")
    vsix.add_argument("vsixfile")
    report = sub.add_parser("report", help="verify a certify_platform.py JSON report")
    report.add_argument("--check", action="append", default=[], help="name required in checks_passed (repeatable)")
    report.add_argument("--flag", action="append", default=[], help="key that must be true (repeatable)")
    report.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                        help="string field that must equal VALUE (repeatable)")
    report.add_argument("reportfile")
    return parser


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return 2 if error.code not in (0, None) else 0
    if args.mode == "junit":
        try:
            requirements = [parse_requirement(item) for item in args.require]
        except ValueError as error:
            print(f"usage error: {error}", file=sys.stderr)
            return 2
        if not requirements or (args.min_total is not None and args.min_total < 1):
            print("usage error: give at least one --require FILE.py and a positive --min-total", file=sys.stderr)
            return 2
        required = requirements
        problems = check_junit(args.xml, requirements, args.min_total or 0)
        ok = f"OK: {len(required)} required test file(s) executed with no skips or failures"
    elif args.mode == "log":
        if not args.marker and not args.forbid:
            print("usage error: at least one --marker or --forbid is needed", file=sys.stderr)
            return 2
        try:
            for pattern in args.forbid:
                re.compile(pattern)
        except re.error as error:
            print(f"usage error: invalid --forbid regex: {error}", file=sys.stderr)
            return 2
        problems = check_log(args.logfile, args.marker, args.forbid)
        ok = f"OK: {len(args.marker)} execution marker(s) found, no forbidden output"
    elif args.mode == "node":
        if args.min_tests is None or args.min_tests < 1:
            print("usage error: give a positive --min-tests", file=sys.stderr)
            return 2
        problems = check_node_tests(args.logfile, args.min_tests)
        ok = f"OK: node:test ran at least {args.min_tests} tests with no failures, skips or todos"
    elif args.mode == "vsix":
        problems = check_vsix(args.vsixfile, args.source_package)
        ok = "OK: VSIX is a real archive with manifest, identity and entry point"
    else:
        if not args.check and not args.flag and not args.expect:
            print("usage error: give at least one --check, --flag or --expect", file=sys.stderr)
            return 2
        problems = check_report(args.reportfile, args.check, args.flag, args.expect)
        ok = "OK: certification report shows the required checks ran"
    if problems:
        print("GATE DID NOT PROVE EXECUTION - a skipped or missing test must not pass CI:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
