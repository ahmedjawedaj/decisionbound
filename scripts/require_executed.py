#!/usr/bin/env python3
"""Fail closed when a connected CI gate passes without really executing its tests.

Optional integration tests skip silently when Docker, Chromium, the MCP SDK or an
opt-in environment variable is missing, and a skipped test still leaves a green
job. This guard turns that into a failure. It is standard library only.

Modes:
  junit   Read pytest JUnit XML. Every named test file must have produced at
          least one test, and no test may be skipped, failed or errored.
  log     Require literal markers in a captured log. Used for the real VS Code
          Extension Development Host run, which prints no JUnit file.
  vsix    Require the packaged VSIX to be a real zip containing the extension
          manifest and a non-empty entry point.
  report  Validate the JSON printed by scripts/certify_platform.py: required
          checks present, required flags true, no failed certification.

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
            reason = message if (not body or body in message) else (f"{message}: {body}" if message else body)
            return label, reason.replace("\n", " ")[:240]
    return "PASSED", ""


def check_junit(xml_paths, required_files) -> list[str]:
    """Return a list of human readable violations (empty means the gate really ran)."""
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
    for required in required_files:
        module = _module_name(required)
        mine = [item for item in flat if _matches(module, item[0], item[1])]
        if not mine:
            problems.append(f"{required}: produced zero tests (collected nothing or never ran)")
            continue
        for classname, name, element in mine:
            label, reason = _status(element)
            if label != "PASSED":
                ident = f"{classname}::{name}" if classname else name
                detail = f" - {reason}" if reason else ""
                problems.append(f"{required}: {ident} {label}{detail}")
    # Any skip, failure or error anywhere in the supplied results is also a violation.
    required_modules = [_module_name(item) for item in required_files]
    for classname, name, element in flat:
        label, reason = _status(element)
        if label != "PASSED" and not any(_matches(m, classname, name) for m in required_modules):
            ident = f"{classname}::{name}" if classname else name
            detail = f" - {reason}" if reason else ""
            problems.append(f"unlisted test {ident} {label}{detail}")
    return problems


def check_log(log_path, markers) -> list[str]:
    """Require every literal marker to appear in the log (not as a prefix of a longer token)."""
    path = Path(log_path)
    if not path.is_file():
        return [f"Log file not found: {path} (the step did not capture output)"]
    text = _read_text(path)
    problems = []
    for marker in markers:
        if not re.search(re.escape(marker) + r"(?!\w)", text):
            problems.append(f"Required evidence missing from {path}: {marker!r}")
    return problems


def check_report(report_path, required_checks, required_flags) -> list[str]:
    """Validate a certify_platform.py JSON report."""
    path = Path(report_path)
    if not path.is_file():
        return [f"Certification report not found: {path}"]
    try:
        text = _read_text(path)
        start = text.find("{")
        data = json.loads(text[start:] if start >= 0 else text)
    except (ValueError, UnicodeDecodeError) as error:
        return [f"Cannot parse certification report {path}: {error}"]
    if not isinstance(data, dict):
        return [f"Certification report {path} is not a JSON object"]
    problems = []
    if data.get("certification") == "failed":
        problems.append(f"Certification reported failure: {data.get('reason', 'unknown')}")
    passed = data.get("checks_passed")
    passed = passed if isinstance(passed, list) else []
    for check in required_checks:
        if check not in passed:
            problems.append(f"Required certification check did not run: {check}")
    for flag in required_flags:
        if data.get(flag) is not True:
            problems.append(f"Required certification flag is not true: {flag}")
    return problems


def check_vsix(vsix_path) -> list[str]:
    """Verify a VSIX archive is real and carries the manifest and entry point."""
    path = Path(vsix_path)
    if not path.is_file():
        return [f"VSIX not found: {path} (packaging did not produce an artifact)"]
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            problems = [f"VSIX lacks {required}" for required in ("extension.vsixmanifest", "extension/package.json")
                        if required not in names]
            if "extension/package.json" in names:
                manifest = json.loads(archive.read("extension/package.json").decode("utf-8-sig"))
                main = str(manifest.get("main", "")).replace("\\", "/")
                entry = "extension/" + main[2:] if main.startswith("./") else "extension/" + main
                if not main or entry not in names:
                    problems.append(f"VSIX lacks its declared entry point {main!r}")
                elif not archive.read(entry).strip():
                    problems.append(f"VSIX entry point {main!r} is empty")
            return problems
    except (zipfile.BadZipFile, ValueError) as error:
        return [f"VSIX {path} is not a readable archive: {error}"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    junit = sub.add_parser("junit", help="verify named test files executed and passed")
    junit.add_argument("--require", action="append", default=[], metavar="TEST_FILE",
                       help="test file that must produce at least one passing test (repeatable)")
    junit.add_argument("xml", nargs="+", help="pytest JUnit XML file(s)")
    log = sub.add_parser("log", help="verify literal markers appear in a captured log")
    log.add_argument("--marker", action="append", default=[], help="literal text that must appear in the log (repeatable)")
    log.add_argument("logfile")
    vsix = sub.add_parser("vsix", help="verify a packaged VSIX archive")
    vsix.add_argument("vsixfile")
    report = sub.add_parser("report", help="verify a certify_platform.py JSON report")
    report.add_argument("--check", action="append", default=[], help="name required in checks_passed (repeatable)")
    report.add_argument("--flag", action="append", default=[], help="key that must be true (repeatable)")
    report.add_argument("reportfile")
    return parser


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return 2 if error.code not in (0, None) else 0
    if args.mode == "junit":
        required = [item for item in args.require]
        if not required or any(not item.strip() for item in required):
            print("usage error: at least one non-empty --require TEST_FILE is needed", file=sys.stderr)
            return 2
        problems = check_junit(args.xml, required)
        ok = f"OK: {len(required)} required test file(s) executed with no skips or failures"
    elif args.mode == "log":
        if not args.marker:
            print("usage error: at least one --marker is needed", file=sys.stderr)
            return 2
        problems = check_log(args.logfile, args.marker)
        ok = f"OK: {len(args.marker)} execution marker(s) found"
    elif args.mode == "vsix":
        problems = check_vsix(args.vsixfile)
        ok = "OK: VSIX is a real archive with manifest and entry point"
    else:
        if not args.check and not args.flag:
            print("usage error: give at least one --check or --flag", file=sys.stderr)
            return 2
        problems = check_report(args.reportfile, args.check, args.flag)
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
