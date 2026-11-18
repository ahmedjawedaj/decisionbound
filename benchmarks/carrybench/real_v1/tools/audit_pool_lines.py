#!/usr/bin/env python3
"""Audit the line pointers of the mined CarryBench config-fact pools.

The miner finds a line by searching a file for the first line that contains a
needle such as `"type"` or `line-length`. The first match is often another
line: a `"type": "git"` inside `repository`, a script that runs a tool, or a
comment that mentions a table. The pool rows carry no text hash, so nothing
caught this. This tool reads each pointed-to file at its pinned commit and
checks that the recorded line is the line a reader would point to, using the
same fact tables as the miner. It then suggests the correct line.

It checks structure and values, not meaning. A pointer that passes names a
line that holds the claimed key with the recorded value in the right place.
It does not say the pool row was a good case.

  python3 -I tools/audit_pool_lines.py --clones-dir ./clones \
      --pool config_facts_v1.jsonl --pool ../real_v2_holdout/config_facts_v2_holdout.jsonl

`<clones-dir>/<owner>__<repo>` may be a git clone made by `fetch_pins.py` (the
file is read from the pinned commit) or a plain directory holding the files
at the pinned commit under their repository paths. `--list-files` prints the
repo, commit and path of each of the files the audit reads, so the plain
directory can be filled by any means. A pool row whose path leaves its folder,
passes through a symlink or names a malformed commit stops the run with an
error. Standard library only.
No third-party text is written: the output holds paths, line numbers, a short
reason, and the matched key name.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path, PurePosixPath

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mine_decisions", _HERE / "mine_decisions.py")
miner = importlib.util.module_from_spec(_spec)
sys.modules["mine_decisions"] = miner
_spec.loader.exec_module(miner)

DEP_SECTIONS = ("devDependencies", "dependencies")  # the sections the miner reads


# ------------------------------------------------------------------ JSON files

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _json_top_indent(lines: list[str]) -> int | None:
    """Indent of the first key of a pretty-printed top-level object, else None."""
    if not lines or lines[0].strip() != "{":
        return None
    for line in lines[1:]:
        if line.strip() and not line.strip().startswith("}"):
            return _indent(line)
    return None


_KEY = re.compile(r'^[ \t]*"((?:[^"\\]|\\.)*)"\s*:\s*(.*?)\s*,?\s*$')


def _json_entries(lines: list[str]):
    """Yield (line_number, section, key, raw_value) for top-level keys and for
    the keys one level below a top-level object. `section` is None for a
    top-level key and the enclosing top-level key otherwise."""
    top = _json_top_indent(lines)
    if top is None:
        return
    section = None
    for n, line in enumerate(lines, 1):
        m = _KEY.match(line)
        if not m:
            continue
        ind = _indent(line)
        if ind == top:
            section = m.group(1)
            yield n, None, m.group(1), m.group(2)
        elif ind > top and section is not None:
            yield n, section, m.group(1), m.group(2)


def _strip_json_value(raw: str) -> str:
    raw = raw.strip()
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else raw


# ----------------------------------------------------------------- TOML files

_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
_TOML_KV = re.compile(r"^\s*([A-Za-z0-9_.\"'-]+)\s*=\s*(.*?)\s*(#.*)?$")


def _toml_lines(lines: list[str]):
    """Yield (line_number, header, line) for lines that are real TOML, with the
    table header in force. Comment lines and the inside of multi-line strings
    are skipped. A header line is reported with header equal to its own name."""
    header = ""
    in_string = None
    for n, line in enumerate(lines, 1):
        if in_string:
            if in_string in line:
                in_string = None
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for quote in ('"""', "'''"):
            if line.count(quote) % 2 == 1:
                in_string = quote
        m = _HEADER.match(line)
        if m and not _TOML_KV.match(line):
            header = m.group(1).strip()
            yield n, header, line
            continue
        yield n, header, line


def _toml_value(line: str, key: str) -> str | None:
    m = _TOML_KV.match(line)
    if not m or m.group(1).strip("\"'") != key:
        return None
    return m.group(2).strip().strip("\"'")


def _is_header(line: str) -> bool:
    return bool(_HEADER.match(line)) and not _TOML_KV.match(line)


# ---------------------------------------------------------------- per-row rule

def _reverse(facts: dict) -> dict:
    out: dict = {}
    for name, fm in facts.items():
        out.setdefault(fm, []).append(name)
    return out


_DEP_NAMES = _reverse(miner.JS_DEP_FACTS)
_TOOL_NAMES = _reverse(miner.PYPROJECT_TOOL_FACTS)


def _first(iterable, pred):
    for item in iterable:
        if pred(item):
            return item
    return None


def audit_row(row: dict, text: str) -> dict | None:
    """Return None when the recorded line is right, else a dict with `why`,
    `suggested_line` (0 when the file has no line that fits) and `unauditable`
    set when the file cannot be checked by line."""
    lines = text.splitlines()
    n = int(row["line_start"])
    how = row["how"]
    if not row.get("families"):
        return {"why": "row names no family, so it cannot be audited", "suggested_line": 0,
                "unauditable": True}
    fam = row["families"][0]["family"]
    member = row["families"][0]["member"]
    if n < 1 or n > len(lines):
        return {"why": "recorded line is outside the file", "suggested_line": 0}

    def bad(why, suggested=0):
        return {"why": why, "suggested_line": suggested}

    if how in ("package_type_field", "packageManager_field", "dependency_declared"):
        top = _json_top_indent(lines)
        if top is None:
            return {"why": "package.json is not pretty-printed, so lines cannot be audited",
                    "suggested_line": 0, "unauditable": True}
        entries = list(_json_entries(lines))
        if how == "package_type_field":
            want = _first(entries, lambda e: e[1] is None and e[2] == "type" and _strip_json_value(e[3]) == member)
            if want and want[0] == n:
                return None
            return bad('line is not the top-level "type" key', want[0] if want else 0)
        if how == "packageManager_field":
            want = _first(entries, lambda e: e[1] is None and e[2] == "packageManager"
                          and _strip_json_value(e[3]).split("@", 1)[0] == member)
            if want and want[0] == n:
                return None
            return bad("line is not the top-level packageManager key", want[0] if want else 0)
        names = _DEP_NAMES.get((fam, member), [])
        want = _first(entries, lambda e: e[1] in DEP_SECTIONS and e[2] in names)
        ok = _first(entries, lambda e: e[0] == n and e[1] in DEP_SECTIONS and e[2] in names)
        if ok:
            return None
        return bad("line is not an entry of devDependencies or dependencies for this tool",
                   want[0] if want else 0)

    toml = list(_toml_lines(lines))
    at = {ln: (hdr, text_line) for ln, hdr, text_line in toml}

    def under(pred_header, key):
        """First line with `key` under a header accepted by pred_header."""
        for ln, hdr, text_line in toml:
            if not _is_header(text_line) and pred_header(hdr) and _toml_value(text_line, key) is not None:
                return ln
        return 0

    if how == "pyproject_tool_table":
        names = _TOOL_NAMES.get((fam, member), [])
        if member == "ruff_format":
            prefixes = ["tool.ruff.format"]
        else:
            prefixes = [f"tool.{name}" for name in names]

        def is_table(hdr):
            return any(hdr == p or hdr.startswith(p + ".") for p in prefixes)

        want = _first(toml, lambda t: _is_header(t[2]) and is_table(t[1]))
        here = at.get(n)
        if here and _is_header(here[1]) and is_table(here[0]):
            return None
        return bad("line is not a header of the tool's table", want[0] if want else 0)

    if how == "requires_python":
        want = under(lambda h: h == "project", "requires-python")
        here = at.get(n)
        value = _toml_value(here[1], "requires-python") if here else None
        if here and here[0] == "project" and value is not None and value[:24] == member:
            return None
        return bad("line is not requires-python under [project]", want)

    if how in ("ruff_line_length", "black_line_length"):
        owner = "ruff" if how == "ruff_line_length" else "black"
        want = under(lambda h: h == f"tool.{owner}", "line-length")
        here = at.get(n)
        value = _toml_value(here[1], "line-length") if here else None
        if here and here[0] == f"tool.{owner}" and value is not None and value == member:
            return None
        return bad(f"line is not line-length under [tool.{owner}]", want)

    if how == "ruff_pydocstyle_convention":
        accepted = {"tool.ruff.lint.pydocstyle", "tool.ruff.pydocstyle"}
        want = under(lambda h: h in accepted, "convention")
        here = at.get(n)
        value = _toml_value(here[1], "convention") if here else None
        if value is not None:
            value = {"pep257": "sphinx"}.get(value.lower(), value.lower())
        if here and here[0] in accepted and value == member:
            return None
        return bad("line is not the pydocstyle convention", want)

    return bad(f"no audit rule for {how}")


# ----------------------------------------------------------------------- run

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _safe_relative(path: str) -> PurePosixPath:
    """A repository path that stays inside its folder, else ValueError."""
    rel = PurePosixPath(path)
    if not path or rel.is_absolute() or "\\" in path or ".." in rel.parts or any(c in path for c in "\0\n\t\r"):
        raise ValueError(f"unsafe repository path in a pool row: {path!r}")
    return rel


def _check_row_location(repo: str, sha: str, path: str) -> PurePosixPath:
    """Validate the repository, commit and path of a pool row, else ValueError."""
    rel = _safe_relative(path)
    if not _SHA.fullmatch(sha):
        raise ValueError(f"unsafe commit in a pool row: {sha!r}")
    if repo.count("/") != 1 or not all(repo.split("/")) or ".." in repo or "\n" in repo or "\t" in repo:
        raise ValueError(f"unsafe repository name in a pool row: {repo!r}")
    return rel


def _load(clones: Path, repo: str, sha: str, path: str) -> str | None:
    """Text of `path` at `sha`, or None when it is not there. A pool row that names
    a path outside its folder, a symlink, or a malformed commit is an error."""
    rel = _check_row_location(repo, sha, path)
    folder = clones / repo.replace("/", "__")
    if (folder / ".git").exists():
        return miner.read_blob(folder, sha, path)
    current = folder
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink in the path of a pool row: {path!r}")
    return current.read_text(encoding="utf-8", errors="replace") if current.is_file() else None


def list_files(pools: list[Path]) -> list[tuple[str, str, str]]:
    """The (repo, sha, path) of every file the audit reads, sorted and unique."""
    wanted = set()
    for pool in pools:
        for raw in pool.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if int(row.get("line_start", 0)) > 0 and row.get("kind") == "config_fact":
                    _check_row_location(row["repo"], row["sha"], row["path"])
                    wanted.add((row["repo"], row["sha"], row["path"]))
    return sorted(wanted)


def audit_pools(clones: Path, pools: list[Path]) -> dict:
    report = {"format": "carrybench-pool-line-audit-v1", "pools": [p.name for p in pools],
              "checked": 0, "ok": 0, "mismatches": [], "unauditable": [], "missing_source": [],
              "by_how": {}}
    seen = set()
    for pool in pools:
        for raw in pool.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if int(row.get("line_start", 0)) <= 0 or row.get("kind") != "config_fact":
                continue
            ident = (pool.name, row["id"])
            if ident in seen:
                continue
            seen.add(ident)
            text = _load(clones, row["repo"], row["sha"], row["path"])
            head = {"pool": pool.name, "id": row["id"], "repo": row["repo"], "sha": row["sha"],
                    "path": row["path"], "how": row["how"], "recorded_line": int(row["line_start"])}
            if text is None:
                report["missing_source"].append(head)
                continue
            result = audit_row(row, text)
            stat = report["by_how"].setdefault(row["how"], {"checked": 0, "mismatch": 0})
            if result and result.get("unauditable"):
                report["unauditable"].append({**head, "why": result["why"]})
                continue
            report["checked"] += 1
            stat["checked"] += 1
            if result is None:
                report["ok"] += 1
            else:
                stat["mismatch"] += 1
                report["mismatches"].append({**head, "why": result["why"],
                                             "suggested_line": result["suggested_line"]})
    report["mismatches"].sort(key=lambda m: (m["pool"], m["repo"], m["path"], m["recorded_line"], m["id"]))
    report["by_how"] = dict(sorted(report["by_how"].items()))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clones-dir", type=Path, default=Path("."))
    ap.add_argument("--pool", type=Path, action="append", required=True)
    ap.add_argument("--list-files", action="store_true",
                    help="print repo, commit and path (tab separated) of each file the audit reads, then stop")
    ap.add_argument("--out", type=Path, help="write the report here instead of stdout")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any pointer is wrong, a source is missing or a row cannot be audited")
    args = ap.parse_args(argv)
    if args.list_files:
        for repo, sha, path in list_files(args.pool):
            print(f"{repo}\t{sha}\t{path}")
        return 0
    report = audit_pools(args.clones_dir, args.pool)
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(f"checked {report['checked']}: {report['ok']} ok, {len(report['mismatches'])} wrong, "
          f"{len(report['unauditable'])} unauditable, {len(report['missing_source'])} missing source",
          file=sys.stderr)
    bad = report["mismatches"] or report["missing_source"] or report["unauditable"]
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
