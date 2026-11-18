#!/usr/bin/env python3
"""TransferBench-Real mining pipeline (pilot).

Extracts candidate engineering-decision statements and conflict signals from
pinned public repositories, using only the git CLI and the Python 3.11
standard library. It never executes third-party code and, by default, never
writes third-party text to its output: records carry repository URL, commit
SHA, path, line range, SPDX id, a machine-made label and SHA-256 hashes.

Subcommands
  inventory  licence, language and decision-signal survey -> repos.csv
  mine       decision statements, ADR records, config facts, reverts -> JSONL
  pair       cross-repository candidate pairs (conflicting / compatible) -> JSONL
  baseline   trivial baselines scored against PROPOSED labels of a case file
  verify     re-hash every provenance pointer of a case file against clones

All repository content is read from git objects at the pinned commit
(`git ls-tree`, `git cat-file`), never from the working tree, so a dirty
checkout cannot change the result. Output is sorted and has no timestamps, so
two runs over the same pins are byte-identical.

Clones are expected at <clones-dir>/<owner>__<repo>. A blobless shallow clone
(`git clone --depth 1 --filter=blob:none --no-checkout` plus a sparse checkout
of the signal files) is enough for everything except `reverts`, which needs
history (`git fetch --depth N`).

Every label this tool or the pilot emits is PROPOSED and UNVALIDATED.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

SCHEMA = "transferbench-real-mined-v0"
PAIR_SCHEMA = "transferbench-real-pair-v0"
ALLOWED_SPDX = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0")
LABELS = ("apply", "translate", "abstain", "escalate")
MAX_BLOB = 400_000  # bytes; larger signal files are skipped and reported

# --------------------------------------------------------------------------- git


def _git(repo: Path, *args: str, check: bool = True) -> str:
    """Run a read-only git command. Uses -c flags only, never global config."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if env.get("TBR_ALLOW_LAZY_FETCH", "") != "1":
        env["GIT_NO_LAZY_FETCH"] = "1"  # honoured by git >= 2.45; older git may lazily fetch a missing blob
    env["LC_ALL"] = "C"
    cmd = ["git", "-c", "core.quotepath=off", "-c", "advice.detachedHead=false",
           "-C", str(repo), *args]
    proc = subprocess.run(cmd, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: "
                           f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout.decode("utf-8", "replace")


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def list_tree(repo: Path, sha: str) -> list[tuple[str, int]]:
    """Return sorted (path, size) for every blob at the commit. Size is -1 when
    the blob is not present locally (partial clone)."""
    out = _git(repo, "ls-tree", "-r", "--format=%(objecttype)%x09%(objectname)%x09%(path)", sha)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "blob":
            rows.append((parts[2], -1))
    rows.sort()
    return rows


def read_blob(repo: Path, sha: str, path: str) -> str | None:
    """Text of path at sha, or None if absent, missing locally, binary or huge."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if env.get("TBR_ALLOW_LAZY_FETCH", "") != "1":
        env["GIT_NO_LAZY_FETCH"] = "1"
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{sha}:{path}"],
                          capture_output=True, env=env)
    if proc.returncode != 0:
        return None
    data = proc.stdout
    if len(data) > MAX_BLOB or b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_text(text: str) -> str:
    """Whitespace-normalised form used for every text hash."""
    return re.sub(r"\s+", " ", text).strip()


def line_span_hash(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    return sha256_text(norm_text("\n".join(lines[start - 1:end])))


# ----------------------------------------------------------------------- licence

_LICENSE_NAMES = re.compile(r"^(LICEN[SC]E|COPYING|UNLICENSE)([._-](?!header).*)?$", re.I)
_NONPERMISSIVE = [
    ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("LGPL", r"GNU LESSER GENERAL PUBLIC LICENSE"),
    ("GPL", r"GNU GENERAL PUBLIC LICENSE"),
    ("BUSL-1.1", r"Business Source License"),
    ("Elastic-2.0", r"Elastic License"),
    ("SSPL-1.0", r"Server Side Public License"),
    ("Commons-Clause", r"Commons Clause"),
    ("SUL", r"Sustainable Use License"),
    ("FSL", r"Functional Source License"),
    ("PolyForm", r"PolyForm"),
]


def classify_license(text: str) -> tuple[str, list[str]]:
    """Heuristic SPDX id from licence text. Returns (spdx or UNKNOWN, flags).
    A human must still read the file: this only narrows the reading."""
    flags: list[str] = []
    flat = norm_text(text)
    for spdx, pat in _NONPERMISSIVE:
        if re.search(pat, flat, re.I):
            flags.append(f"mentions:{spdx}")
    hits: list[str] = []
    if re.search(r"Apache License,? Version 2\.0|Apache License\s+Version 2\.0", flat, re.I):
        hits.append("Apache-2.0")
    if re.search(r"Mozilla Public License,? (Version|v\.?) ?2\.0", flat, re.I):
        hits.append("MPL-2.0")
    if re.search(r"Permission is hereby granted, free of charge, to any person obtaining", flat, re.I):
        hits.append("MIT")
    if re.search(r"Redistribution and use in source and binary forms", flat, re.I):
        if re.search(r"Neither the name|names? of (its|the) contributors|may not be used to endorse", flat, re.I):
            hits.append("BSD-3-Clause")
        else:
            hits.append("BSD-2-Clause")
    if re.search(r"enterprise|commercial licen[sc]e|\bee/", flat, re.I):
        flags.append("mentions:enterprise-or-commercial")
    if len(hits) == 1:
        return hits[0], flags
    if len(hits) > 1:
        flags.append("multiple:" + "+".join(hits))
        return " AND/OR ".join(hits), flags
    return "UNKNOWN", flags


def license_report(repo: Path, sha: str, tree: list[tuple[str, int]]) -> dict:
    files = [p for p, _ in tree if "/" not in p and _LICENSE_NAMES.match(p)]
    found = []
    for path in files:
        text = read_blob(repo, sha, path)
        if text is None:
            found.append({"path": path, "spdx": "UNREADABLE", "flags": [], "sha256": None})
            continue
        spdx, flags = classify_license(text)
        found.append({"path": path, "spdx": spdx, "flags": flags,
                      "sha256": sha256_text(norm_text(text))})
    spdx_set = sorted({f["spdx"] for f in found})
    nested = sorted(p for p, _ in tree if "/" in p and _LICENSE_NAMES.match(p.rsplit("/", 1)[1]))
    return {"files": found, "spdx": " + ".join(spdx_set) if spdx_set else "NONE",
            "nested_license_files": len(nested),
            "nested_license_dirs": sorted({p.split("/", 1)[0] for p in nested})[:12]}


# --------------------------------------------------------------- signal taxonomy

ADR_DIR = re.compile(r"(^|/)(adrs?|decisions|architecture[-_]decisions?|architectural[-_]decisions?|"
                     r"decision[-_]records?|architecture/decisions)/", re.I)
RFC_DIR = re.compile(r"(^|/)rfcs?/", re.I)
AGENT_FILE = re.compile(r"(^|/)(AGENTS\.md|Agents\.md|CLAUDE\.md|GEMINI\.md|\.cursorrules|\.clinerules|"
                        r"copilot-instructions\.md)$|(^|/)\.cursor/rules/[^/]+$|^\.[a-z]+/agents\.md$")
CONTRIB_FILE = re.compile(r"^(\.github/|docs/)?CONTRIBUTING(\.md|\.rst|\.txt)?$", re.I)
DOC_EXT = re.compile(r"\.(md|mdx|mdc|rst|txt)$", re.I)


def signal_class(path: str) -> str | None:
    if AGENT_FILE.search(path):
        return "agent_instructions"
    if CONTRIB_FILE.match(path):
        return "contributing"
    if ADR_DIR.search(path) and DOC_EXT.search(path):
        return "adr"
    if RFC_DIR.search(path) and DOC_EXT.search(path):
        return "rfc"
    return None


NORMATIVE = re.compile(
    r"\b(must( not)?|never|always|do not|don't|dont|should( not|n't)?|avoid|prefer(red)?|"
    r"required?|forbidden|prohibited|not allowed|only use|instead of|rather than|deprecated|"
    r"no longer|we (will|decided|chose|use|adopt)|is (banned|disallowed)|make sure|ensure)\b", re.I)
NEG_CUE = re.compile(
    r"\b(do not|don't|dont|never|avoid|not|no|instead of|rather than|without|banned|forbidden|"
    r"prohibited|deprecated|drop(ped)?|remove[ds]?|migrat\w+ (from|off))\b", re.I)
CLAUSE_BREAK = re.compile(r"[.;:!?\u2014\u2013]|\b(but|prefer|always|only)\b", re.I)


def negated(before: str) -> bool:
    """True when the last negation cue in the window reaches the mention without a
    clause break, so 'never npm or yarn' proscribes both tools."""
    last = None
    for m in NEG_CUE.finditer(before):
        last = m
    return last is not None and not CLAUSE_BREAK.search(before[last.end():])


# family -> member -> regex. A family is a set of mutually exclusive choices.
FAMILIES: dict[str, dict[str, str]] = {
    "js_package_manager": {
        "pnpm": r"\bpnpm\b",
        "yarn": r"\byarn\b",
        "npm": r"\bnpm (install|ci|run|test|i)\b",
        "bun": r"\bbun (install|run|x|add)\b|\bbunx\b",
    },
    "py_env_manager": {
        "uv": r"\buv (run|sync|pip|add|lock|venv|tool|build)\b|\buvx\b",
        "poetry": r"\bpoetry (run|install|add|lock|build)\b",
        "hatch": r"\bhatch (run|env|test|fmt|build)\b",
        "pdm": r"\bpdm (run|install|add|sync)\b",
        "pip": r"\bpip install\b",
        "conda": r"\b(conda|mamba) (install|env|create|activate)\b",
        "pixi": r"\bpixi (run|install|add)\b",
    },
    "py_formatter": {
        "ruff_format": r"\bruff format\b",
        "black": r"\bblack\b(?! box)",
        "yapf": r"\byapf\b",
        "autopep8": r"\bautopep8\b",
    },
    "py_linter": {
        "ruff": r"\bruff( check)?\b(?! format)",
        "flake8": r"\bflake8\b",
        "pylint": r"\bpylint\b",
    },
    "py_typechecker": {
        "mypy": r"\bmypy\b",
        "pyright": r"\bpyright\b",
        "pyrefly": r"\bpyrefly\b",
        "pytype": r"\bpytype\b",
    },
    "py_test_runner": {
        "pytest": r"\bpytest\b",
        "unittest": r"\bunittest\b(?!\.mock)",
    },
    "js_test_runner": {
        "vitest": r"\bvitest\b",
        "jest": r"\bjest\b",
        "mocha": r"\bmocha\b",
        "node_test": r"\bnode:test\b|\bnode --test\b",
        "bun_test": r"\bbun test\b",
    },
    "js_linter": {
        "eslint": r"\beslint\b",
        "biome": r"\bbiome\b",
        "oxlint": r"\boxlint\b",
        "tslint": r"\btslint\b",
    },
    "js_formatter": {
        "prettier": r"\bprettier\b",
        "dprint": r"\bdprint\b",
        "oxfmt": r"\boxfmt\b",
        "biome": r"\bbiome (format|check)\b",
    },
    "change_record": {
        "changesets": r"\bchangesets?\b",
        "towncrier": r"\btowncrier\b|\bnews ?fragments?\b|\bnewsfragments?\b",
        "conventional_commits": r"\bconventional[- ]commits?\b",
        "manual_changelog": r"\b(update|edit|add (an? )?entry (to|in)) (the )?(CHANGELOG|CHANGES|HISTORY)\b",
    },
    "py_docstring_style": {
        "google": r"\bgoogle[- ]style\b",
        "numpy": r"\bnumpy(doc)?[- ]style\b|\bnumpydoc\b",
        "sphinx": r"\b(sphinx|reST|reStructuredText)[- ]style\b",
    },
    # single-member families: polarity (prescribe / proscribe) is the choice
    "practice_mocking": {"mocking": r"\bmock(s|ing|ed)?\b"},
    "practice_ts_any": {"any_type": r"`any`|\bas any\b|\bany type\b|\bexplicit any\b"},
    "practice_ts_enum": {"enum": r"\benums?\b"},
    "practice_default_export": {"default_export": r"\bdefault exports?\b|\bexport default\b"},
    "practice_force_push": {"force_push": r"\bforce[- ]push(es|ing)?\b|--force\b"},
    "practice_type_ignore": {"type_ignore": r"type: ?ignore|@ts-ignore|@ts-expect-error|\bnoqa\b"},
    "practice_emoji": {"emoji": r"\bemojis?\b"},
    "practice_relative_imports": {"relative_import": r"\brelative imports?\b"},
    "practice_comments": {"code_comments": r"\b(add|write|adding|writing|unnecessary|redundant|obvious) comments?\b"},
    "practice_squash": {"squash_merge": r"\bsquash(ed|ing)?\b"},
    "practice_print": {"print_logging": r"\bconsole\.log\b|\bprint\(\)|\bprint statements?\b"},
    "practice_async": {"async_only": r"\basync[- ]only\b|\bsync wrappers?\b"},
    "practice_barrel": {"barrel_file": r"\bbarrel (files?|exports?)\b"},
    "practice_new_dependency": {"new_dependency": r"\bnew (runtime )?dependenc(y|ies)\b|\badd(ing)? (a )?dependenc(y|ies)\b"},
    "practice_ai_disclosure": {"ai_generated": r"\b(AI|LLM)[- ](generated|assisted|written)\b|\bgenerated (by|with) (an? )?(AI|LLM)\b"},
}
FAMILY_RX = {fam: {m: re.compile(rx, re.I) for m, rx in members.items()}
             for fam, members in FAMILIES.items()}

LOCKFILES = {
    "pnpm-lock.yaml": ("js_package_manager", "pnpm"),
    "yarn.lock": ("js_package_manager", "yarn"),
    "package-lock.json": ("js_package_manager", "npm"),
    "bun.lock": ("js_package_manager", "bun"),
    "bun.lockb": ("js_package_manager", "bun"),
    "uv.lock": ("py_env_manager", "uv"),
    "poetry.lock": ("py_env_manager", "poetry"),
    "pdm.lock": ("py_env_manager", "pdm"),
    "pixi.lock": ("py_env_manager", "pixi"),
}
ROOT_CONFIG = [  # (regex on root file name, family, member)
    (r"^(ruff|\.ruff)\.toml$", "py_linter", "ruff"),
    (r"^\.flake8$", "py_linter", "flake8"),
    (r"^(\.pylintrc|pylintrc)$", "py_linter", "pylint"),
    (r"^(mypy\.ini|\.mypy\.ini)$", "py_typechecker", "mypy"),
    (r"^pyrightconfig\.json$", "py_typechecker", "pyright"),
    (r"^pyrefly\.toml$", "py_typechecker", "pyrefly"),
    (r"^biome\.jsonc?$", "js_linter", "biome"),
    (r"^(eslint\.config\.[cm]?[jt]s|\.eslintrc(\.[a-z]+)?)$", "js_linter", "eslint"),
    (r"^\.oxlintrc\.json$", "js_linter", "oxlint"),
    (r"^(\.prettierrc(\.[a-z]+)?|prettier\.config\.[cm]?[jt]s)$", "js_formatter", "prettier"),
    (r"^dprint\.jsonc?$", "js_formatter", "dprint"),
    (r"^\.oxfmtrc\.jsonc?$", "js_formatter", "oxfmt"),
    (r"^jest\.config\.[cm]?[jt]s$", "js_test_runner", "jest"),
    (r"^vitest\.(config|workspace)\.[cm]?[jt]s$", "js_test_runner", "vitest"),
    (r"^\.mocharc(\.[a-z]+)?$", "js_test_runner", "mocha"),
]
JS_DEP_FACTS = {
    "vitest": ("js_test_runner", "vitest"), "jest": ("js_test_runner", "jest"),
    "mocha": ("js_test_runner", "mocha"), "eslint": ("js_linter", "eslint"),
    "@biomejs/biome": ("js_linter", "biome"), "oxlint": ("js_linter", "oxlint"),
    "prettier": ("js_formatter", "prettier"), "dprint": ("js_formatter", "dprint"),
    "oxfmt": ("js_formatter", "oxfmt"), "@changesets/cli": ("change_record", "changesets"),
}
PYPROJECT_TOOL_FACTS = {
    "ruff": ("py_linter", "ruff"), "flake8": ("py_linter", "flake8"),
    "pylint": ("py_linter", "pylint"), "black": ("py_formatter", "black"),
    "yapf": ("py_formatter", "yapf"), "mypy": ("py_typechecker", "mypy"),
    "pyright": ("py_typechecker", "pyright"), "pyrefly": ("py_typechecker", "pyrefly"),
    "pytest": ("py_test_runner", "pytest"), "towncrier": ("change_record", "towncrier"),
    "uv": ("py_env_manager", "uv"), "poetry": ("py_env_manager", "poetry"),
    "hatch": ("py_env_manager", "hatch"), "pdm": ("py_env_manager", "pdm"),
    "pixi": ("py_env_manager", "pixi"),
}


def rec_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def find_line(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 0


# ---------------------------------------------------------------- statement mining

def classify_mentions(line: str) -> list[tuple[str, str, str]]:
    """(family, member, polarity) for every family member named on the line.
    Polarity is 'proscribe' when a negation cue sits directly before the
    mention, else 'prescribe'. Deliberately shallow: it is a candidate
    generator and an annotator decides."""
    out = []
    for fam in sorted(FAMILY_RX):
        for member in sorted(FAMILY_RX[fam]):
            m = FAMILY_RX[fam][member].search(line)
            if not m:
                continue
            polarity = "proscribe" if negated(line[max(0, m.start() - 60):m.start()]) else "prescribe"
            out.append((fam, member, polarity))
    return out


def iter_statements(text: str):
    """Yield (line_start, line_end, joined_text, in_code_block) for list items,
    paragraphs lines and fenced command lines. A list item absorbs its indented
    continuation lines."""
    lines = text.splitlines()
    in_code = False
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            i += 1
            continue
        if not stripped:
            i += 1
            continue
        if in_code:
            yield i + 1, i + 1, stripped, True
            i += 1
            continue
        start = i
        buf = [stripped]
        is_item = bool(re.match(r"^([-*+]|\d+[.)])\s+", stripped))
        indent = len(raw) - len(raw.lstrip())
        j = i + 1
        while is_item and j < n:
            nxt = lines[j]
            ns = nxt.strip()
            if not ns or ns.startswith("```") or re.match(r"^([-*+]|\d+[.)])\s+", ns) or ns.startswith("#"):
                break
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            buf.append(ns)
            j += 1
        yield start + 1, j, " ".join(buf), False
        i = j


def adr_meta(text: str) -> dict:
    lines = text.splitlines()
    title_line = 0
    for i, line in enumerate(lines[:40], 1):
        if line.startswith("#") or (i < len(lines) and re.match(r"^[=\-]{4,}$", lines[i].strip() or "x")):
            title_line = i
            break
    status = "unknown"
    head = "\n".join(lines[:60])
    m = re.search(r"(?im)^\W*(status|state)\W*[:\n]\W*\**\s*([A-Za-z-]+)", head)
    if m:
        status = m.group(2).lower()
    else:
        m = re.search(r"(?im)^#+\s*status\s*\n+\s*\**([A-Za-z-]+)", text)
        if m:
            status = m.group(1).lower()
    if re.search(r"(?i)supersed", head):
        status = "superseded" if status in ("unknown", "superseded") else status + "+supersede-mention"
    dec_start = dec_end = 0
    for i, line in enumerate(lines, 1):
        if dec_start == 0 and re.match(r"^#+\s*.*\b(decision|outcome)\b", line, re.I) and i != title_line:
            dec_start = i
        elif dec_start and line.startswith("#") and i > dec_start:
            dec_end = i - 1
            break
    if dec_start and not dec_end:
        dec_end = len(lines)
    return {"title_line": title_line, "status": status,
            "decision_start": dec_start, "decision_end": dec_end}


def mine_doc(repo_name: str, url: str, sha: str, spdx: str, path: str, sclass: str,
             text: str, include_text: bool) -> list[dict]:
    recs: list[dict] = []
    base = {"format": SCHEMA, "repo": repo_name, "url": url, "sha": sha, "spdx": spdx, "path": path,
            "signal_class": sclass}
    dec_range = None
    if sclass in ("adr", "rfc"):
        meta = adr_meta(text)
        nlines = len(text.splitlines())
        r = dict(base, kind="adr_record", line_start=1, line_end=nlines,
                 text_sha256=sha256_text(norm_text(text)), adr_status=meta["status"],
                 title_line=meta["title_line"], decision_start=meta["decision_start"],
                 decision_end=meta["decision_end"], families=[], auto_label=f"{sclass} record")
        r["id"] = rec_id(repo_name, sha, path, 1, nlines, "adr_record")
        recs.append(r)
        if meta["decision_start"]:
            dec_range = (meta["decision_start"], meta["decision_end"])
    for start, end, joined, in_code in iter_statements(text):
        mentions = classify_mentions(joined)
        normative = bool(NORMATIVE.search(joined)) and not in_code
        if in_code:
            # a fenced command counts as an implied decision only for tool families
            mentions = [m for m in mentions if not m[0].startswith("practice_")]
            if not mentions:
                continue
            kind = "command"
        elif normative and mentions:
            kind = "statement"
        elif normative and sclass == "agent_instructions":
            kind = "statement"
        elif normative and dec_range and dec_range[0] <= start <= dec_range[1]:
            kind = "statement"
        elif mentions and sclass == "agent_instructions" and any(not m[0].startswith("practice_") for m in mentions):
            kind = "mention"
            mentions = [m for m in mentions if not m[0].startswith("practice_")]
        else:
            continue
        if kind == "statement" and len(joined) < 12:
            continue
        r = dict(base, kind=kind, line_start=start, line_end=end,
                 text_sha256=sha256_text(norm_text(joined)),
                 families=[{"family": f, "member": m, "polarity": p} for f, m, p in mentions],
                 auto_label="; ".join(f"{p} {f}={m}" for f, m, p in mentions) or "normative statement (untyped)")
        if include_text:
            r["text_LOCAL_ONLY_do_not_redistribute"] = joined[:600]
        r["id"] = rec_id(repo_name, sha, path, start, end, kind)
        recs.append(r)
    return recs


# ------------------------------------------------------------------ config facts

def mine_config(repo: Path, repo_name: str, url: str, sha: str, spdx: str,
                tree: list[tuple[str, int]]) -> list[dict]:
    recs: list[dict] = []
    root_files = sorted(p for p, _ in tree if "/" not in p)
    top_dirs = sorted({p.split("/", 1)[0] for p, _ in tree if "/" in p})

    def fact(path: str, line: int, family: str, member: str, how: str, value: str = "") -> None:
        r = {"format": SCHEMA, "repo": repo_name, "url": url, "sha": sha, "spdx": spdx, "path": path,
             "signal_class": "config", "kind": "config_fact", "line_start": line, "line_end": line,
             "text_sha256": None, "how": how, "value": value,
             "families": [{"family": family, "member": member, "polarity": "prescribe"}],
             "auto_label": f"config {family}={member} ({how})"}
        r["id"] = rec_id(repo_name, sha, path, line, family, member, how)
        recs.append(r)

    for name in root_files:
        if name in LOCKFILES:
            fam, mem = LOCKFILES[name]
            fact(name, 0, fam, mem, "lockfile_present")
        for rx, fam, mem in ROOT_CONFIG:
            if re.match(rx, name):
                fact(name, 0, fam, mem, "config_file_present")
    if ".changeset" in top_dirs:
        fact(".changeset", 0, "change_record", "changesets", "directory_present")
    for d in ("newsfragments", "changelog.d", "changes", "news"):
        if d in top_dirs:
            fact(d, 0, "change_record", "towncrier", "fragment_directory_present")

    if "package.json" in root_files:
        text = read_blob(repo, sha, "package.json")
        if text:
            try:
                pkg = json.loads(text)
            except ValueError:
                pkg = {}
            if isinstance(pkg, dict):
                pm = pkg.get("packageManager")
                if isinstance(pm, str) and "@" in pm:
                    member = pm.split("@", 1)[0]
                    if member in FAMILIES["js_package_manager"]:
                        fact("package.json", find_line(text, '"packageManager"'), "js_package_manager",
                             member, "packageManager_field", pm.split("+", 1)[0][:40])
                deps: dict = {}
                for key in ("devDependencies", "dependencies"):
                    if isinstance(pkg.get(key), dict):
                        deps.update(pkg[key])
                for dep in sorted(deps):
                    if dep in JS_DEP_FACTS:
                        fam, mem = JS_DEP_FACTS[dep]
                        fact("package.json", find_line(text, f'"{dep}"'), fam, mem, "dependency_declared")
                if pkg.get("type") in ("module", "commonjs"):
                    fact("package.json", find_line(text, '"type"'), "js_module_system",
                         str(pkg["type"]), "package_type_field")

    if "pyproject.toml" in root_files and tomllib is not None:
        text = read_blob(repo, sha, "pyproject.toml")
        if text:
            try:
                data = tomllib.loads(text)
            except Exception:
                data = {}
            tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
            for name in sorted(tool):
                if name in PYPROJECT_TOOL_FACTS:
                    fam, mem = PYPROJECT_TOOL_FACTS[name]
                    fact("pyproject.toml", find_line(text, f"[tool.{name}"), fam, mem, "pyproject_tool_table")
            ruff = tool.get("ruff", {}) if isinstance(tool.get("ruff"), dict) else {}
            if isinstance(ruff.get("format"), dict):
                fact("pyproject.toml", find_line(text, "[tool.ruff.format"), "py_formatter", "ruff_format",
                     "pyproject_tool_table")
            for owner, table in (("ruff", ruff), ("black", tool.get("black", {}))):
                if isinstance(table, dict) and isinstance(table.get("line-length"), int):
                    fact("pyproject.toml", find_line(text, "line-length"), "py_line_length",
                         str(table["line-length"]), f"{owner}_line_length", str(table["line-length"]))
            lint = ruff.get("lint", {}) if isinstance(ruff.get("lint"), dict) else {}
            pyd = lint.get("pydocstyle", ruff.get("pydocstyle", {}))
            if isinstance(pyd, dict) and isinstance(pyd.get("convention"), str):
                conv = pyd["convention"].lower()
                conv = {"pep257": "sphinx"}.get(conv, conv)
                fact("pyproject.toml", find_line(text, "convention"), "py_docstring_style", conv,
                     "ruff_pydocstyle_convention")
            proj = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
            if isinstance(proj.get("requires-python"), str):
                fact("pyproject.toml", find_line(text, "requires-python"), "py_min_version",
                     proj["requires-python"][:24], "requires_python", proj["requires-python"][:24])
    return recs


# ---------------------------------------------------------------------- reverts

def mine_reverts(repo: Path, repo_name: str, url: str, spdx: str, limit: int,
                 include_text: bool) -> list[dict]:
    out = _git(repo, "log", f"-n{limit}", "-i", "--grep=^Revert", "--format=%H%x1f%s%x1f%b%x1e", check=False)
    recs = []
    for chunk in out.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) != 3 or not parts[0]:
            continue
        csha, subject, body = parts
        m = re.search(r"This reverts commit ([0-9a-f]{7,40})", body)
        expl = re.sub(r"This reverts commit [0-9a-f]{7,40}\.?", "", body)
        expl = "\n".join(ln for ln in expl.splitlines()
                         if not re.match(r"^\s*(Co-authored-by|Signed-off-by|Change-Id|Reviewed-by):", ln, re.I))
        expl = norm_text(expl)
        r = {"format": SCHEMA, "repo": repo_name, "url": url, "sha": csha, "spdx": spdx, "path": None,
             "signal_class": "history", "kind": "revert", "line_start": 0, "line_end": 0,
             "reverted_sha": m.group(1) if m else None, "explanation_chars": len(expl),
             "has_explanation": len(expl) >= 40, "text_sha256": sha256_text(norm_text(subject + " " + body)),
             "families": [{"family": f, "member": mem, "polarity": "revert-mention"}
                          for f, mem, _ in classify_mentions(subject + " " + expl)],
             "auto_label": "revert commit" + (" with explanation" if len(expl) >= 40 else "")}
        if include_text:
            r["text_LOCAL_ONLY_do_not_redistribute"] = (subject + " :: " + expl)[:400]
        r["id"] = rec_id(repo_name, csha, "revert")
        recs.append(r)
    recs.sort(key=lambda r: r["sha"])
    return recs


# --------------------------------------------------------------------- repo walk

def load_repo_list(path: Path) -> list[dict]:
    """CSV with header containing at least 'repo'. Optional: url, sha."""
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("repo") and not row["repo"].startswith("#"):
                rows.append(row)
    rows.sort(key=lambda r: r["repo"].lower())
    return rows


def clone_path(clones_dir: Path, repo: str) -> Path:
    return clones_dir / repo.replace("/", "__")


def language_profile(tree: list[tuple[str, int]]) -> tuple[str, dict]:
    counts = {"py": 0, "ts": 0, "js": 0, "rs": 0, "go": 0}
    for p, _ in tree:
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if ext in ("py", "pyi"):
            counts["py"] += 1
        elif ext in ("ts", "tsx", "mts", "cts"):
            counts["ts"] += 1
        elif ext in ("js", "jsx", "mjs", "cjs"):
            counts["js"] += 1
        elif ext in counts:
            counts[ext] += 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    primary = {"py": "Python", "ts": "TypeScript", "js": "JavaScript", "rs": "Rust", "go": "Go"}[top[0][0]]
    if top[0][1] == 0:
        primary = "unknown"
    second = top[1]
    if second[1] and second[1] * 4 >= top[0][1] and second[0] in ("py", "ts"):
        primary += "+" + {"py": "Python", "ts": "TypeScript"}[second[0]]
    return primary, counts


def signal_files(tree: list[tuple[str, int]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p, _ in tree:
        c = signal_class(p)
        if c:
            out.setdefault(c, []).append(p)
    return out


def cmd_inventory(args: argparse.Namespace) -> int:
    rows = load_repo_list(Path(args.repos))
    clones = Path(args.clones_dir)
    out_rows = []
    for row in rows:
        name = row["repo"]
        repo = clone_path(clones, name)
        if not (repo / ".git").exists():
            print(f"skip (no clone): {name}", file=sys.stderr)
            continue
        sha = row.get("sha") or head_sha(repo)
        tree = list_tree(repo, sha)
        lic = license_report(repo, sha, tree)
        lang, counts = language_profile(tree)
        sig = signal_files(tree)
        ev = []
        for cls in ("adr", "rfc", "agent_instructions", "contributing"):
            paths = sig.get(cls, [])
            if paths:
                ev.append(f"{cls}:{len(paths)}[{paths[0]}]")
        notes = []
        for f in lic["files"]:
            notes.extend(f["flags"])
        if lic["nested_license_files"]:
            notes.append(f"nested_license_files:{lic['nested_license_files']}")
        out_rows.append({
            "repo": name, "url": row.get("url") or f"https://github.com/{name}", "sha": sha,
            "spdx": lic["spdx"], "evidence_files": "; ".join(ev), "language": lang,
            "notes": "; ".join(sorted(set(notes))),
            "license_files": "; ".join(f"{f['path']}#{(f['sha256'] or '')[:12]}" for f in lic["files"]),
            "n_py": counts["py"], "n_ts": counts["ts"],
        })
    fields = ["repo", "url", "sha", "spdx", "evidence_files", "language", "notes", "license_files", "n_py", "n_ts"]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)
    print(f"inventory: {len(out_rows)} repos -> {args.out}", file=sys.stderr)
    return 0


def mine_repo(repo: Path, name: str, url: str, sha: str, spdx: str, include_text: bool,
              revert_limit: int, max_files: int) -> tuple[list[dict], dict]:
    tree = list_tree(repo, sha)
    stats = {"files_seen": 0, "files_unreadable": 0, "files_capped": 0}
    recs: list[dict] = []
    sig = signal_files(tree)
    for cls in sorted(sig):
        paths = sig[cls]
        if len(paths) > max_files:
            stats["files_capped"] += len(paths) - max_files
            paths = paths[:max_files]
        for path in paths:
            text = read_blob(repo, sha, path)
            if text is None:
                stats["files_unreadable"] += 1
                continue
            stats["files_seen"] += 1
            recs.extend(mine_doc(name, url, sha, spdx, path, cls, text, include_text))
    recs.extend(mine_config(repo, name, url, sha, spdx, tree))
    if revert_limit > 0:
        recs.extend(mine_reverts(repo, name, url, spdx, revert_limit, include_text))
    return recs, stats


def cmd_mine(args: argparse.Namespace) -> int:
    rows = load_repo_list(Path(args.repos))
    clones = Path(args.clones_dir)
    allowed = set(ALLOWED_SPDX)
    total = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for row in rows:
            name = row["repo"]
            spdx = row.get("spdx", "")
            if not args.no_license_gate and spdx not in allowed:
                print(f"skip (licence gate: {spdx or 'blank'}): {name}", file=sys.stderr)
                continue
            repo = clone_path(clones, name)
            if not (repo / ".git").exists():
                print(f"skip (no clone): {name}", file=sys.stderr)
                continue
            sha = row.get("sha") or head_sha(repo)
            recs, stats = mine_repo(repo, name, row.get("url") or f"https://github.com/{name}", sha, spdx,
                                    args.include_text, args.reverts, args.max_files_per_class)
            recs.sort(key=lambda r: (r["path"] or "", r["line_start"], r["kind"], r["id"]))
            for r in recs:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
            total += len(recs)
            print(f"mined {name}: {len(recs)} records {stats}", file=sys.stderr)
    print(f"mine: {total} records -> {args.out}", file=sys.stderr)
    return 0


# ----------------------------------------------------------------------- pairing

STOP = frozenset("a an the and or of to in on for with is are be it this that as at by from not no do "
                 "use using used should must will can if when we you your our all any".split())


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9_+.-]{1,}", text.lower()) if t not in STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return round(len(a & b) / len(a | b), 4) if a and b else 0.0


def span_text(clones: Path, repo: str, sha: str, path: str | None, start: int, end: int) -> str:
    if not path or start <= 0:
        return ""
    text = read_blob(clone_path(clones, repo), sha, path)
    if text is None:
        return ""
    return "\n".join(text.splitlines()[start - 1:end])


def cmd_pair(args: argparse.Namespace) -> int:
    recs = [json.loads(line) for line in Path(args.mined).read_text(encoding="utf-8").splitlines() if line.strip()]
    by_repo: dict[str, list[dict]] = {}
    for r in recs:
        by_repo.setdefault(r["repo"], []).append(r)
    # target stance per repo and family: member -> strongest evidence record
    strength = {"config_fact": 3, "statement": 2, "command": 1, "mention": 0}
    stance: dict[str, dict[str, dict[str, dict]]] = {}
    for repo, rs in by_repo.items():
        for r in rs:
            if r["kind"] not in strength:
                continue
            for f in r["families"]:
                key = f["member"] if f["polarity"] == "prescribe" else "!" + f["member"]
                cur = stance.setdefault(repo, {}).setdefault(f["family"], {}).get(key)
                if cur is None or (strength[r["kind"]], cur["id"]) > (strength[cur["kind"]], r["id"]):
                    stance[repo][f["family"]][key] = r
    n = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for src in sorted(by_repo):
            for d in by_repo[src]:
                if d["kind"] != "statement" or d["signal_class"] not in ("agent_instructions", "adr", "contributing", "rfc"):
                    continue
                for f in d["families"]:
                    fam, mem, pol = f["family"], f["member"], f["polarity"]
                    single = len(FAMILIES.get(fam, {})) == 1
                    for tgt in sorted(by_repo):
                        if tgt == src:
                            continue
                        st = stance.get(tgt, {}).get(fam)
                        if not st:
                            continue
                        same = st.get(mem) if pol == "prescribe" else st.get("!" + mem)
                        opposite = st.get("!" + mem) if pol == "prescribe" else st.get(mem)
                        others = sorted(k for k in st if not k.startswith("!") and k != mem)
                        if single:
                            relation = ("mixed" if same and opposite else "compatible" if same
                                        else "conflicting" if opposite else None)
                            ev = same if relation == "compatible" else opposite or same
                        elif pol == "prescribe":
                            relation = ("compatible" if same and not opposite and not others else
                                        "conflicting" if (others or opposite) and not same else "mixed")
                            ev = same if relation == "compatible" else (opposite or (st[others[0]] if others else same))
                        else:  # source proscribes a member
                            relation = ("conflicting" if opposite and not same else
                                        "compatible" if same and not opposite else None)
                            ev = opposite if relation == "conflicting" else same
                        if relation is None or ev is None:
                            continue
                        rec = {"format": PAIR_SCHEMA, "status": "candidate_unlabelled",
                               "family": fam, "source_member": mem, "source_polarity": pol,
                               "auto_relation": relation,
                               "source": {k: d[k] for k in ("repo", "url", "sha", "spdx", "path", "line_start",
                                                            "line_end", "text_sha256", "signal_class", "id")},
                               "target": {"repo": tgt, "url": ev["url"], "sha": ev["sha"], "spdx": ev["spdx"]},
                               "target_evidence": {k: ev.get(k) for k in ("path", "line_start", "line_end",
                                                                         "text_sha256", "kind", "auto_label", "id")},
                               "target_members_present": sorted(st)}
                        if args.clones_dir:
                            clones = Path(args.clones_dir)
                            a = tokens(span_text(clones, src, d["sha"], d["path"], d["line_start"], d["line_end"]))
                            b = tokens(span_text(clones, tgt, ev["sha"], ev.get("path"), ev["line_start"], ev["line_end"]))
                            rec["leakage"] = {"token_jaccard": jaccard(a, b),
                                              "target_evidence_is_text": ev["kind"] != "config_fact"}
                        rec["pair_id"] = rec_id(d["id"], tgt, fam, mem, ev["id"])
                        fh.write(json.dumps(rec, sort_keys=True) + "\n")
                        n += 1
    print(f"pair: {n} candidate pairs -> {args.out}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------- baseline

def _case_texts(case: dict, clones: Path) -> tuple[str, str]:
    s = case["source"]
    src = span_text(clones, s["repo"], s["sha"], s["path"], s["line_start"], s["line_end"])
    t = case["target"]
    parts = [span_text(clones, t["repo"], t["sha"], e.get("path"), e.get("line_start", 0), e.get("line_end", 0))
             for e in t.get("evidence", [])]
    return src, "\n".join(parts)


def cmd_baseline(args: argparse.Namespace) -> int:
    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [c for c in cases if c.get("format", "").startswith(("transferbench-real-case", "carrybench-real-case"))]
    clones = Path(args.clones_dir)
    rows = []
    for c in cases:
        src, tgt = _case_texts(c, clones)
        j = jaccard(tokens(src), tokens(tgt))
        fam_src = {(f, m) for f, m, _ in classify_mentions(src)}
        fam_tgt = {(f, m) for f, m, _ in classify_mentions(tgt)}
        rows.append((c, j, bool(fam_src & fam_tgt), bool(src), bool(tgt)))

    def score(name: str, pred) -> dict:
        exact = transfer = harm = 0
        for c, j, shared, _, _ in rows:
            p = pred(c, j, shared)
            gold = c["proposed_label"]
            allowed = set(c.get("allowed_actions") or [gold])
            exact += p in allowed
            transfer += (p in ("apply", "translate")) == (gold in ("apply", "translate"))
            harm += p == "apply" and "apply" not in allowed
        n = len(rows) or 1
        return {"baseline": name, "n": len(rows), "allowed_action_accuracy": round(exact / n, 3),
                "transfer_vs_withhold_accuracy": round(transfer / n, 3),
                "harmful_apply_rate": round(harm / n, 3)}

    stance: dict[str, set[tuple[str, str]]] = {}
    if args.mined:
        for line in Path(args.mined).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("kind") == "config_fact":
                for f in r["families"]:
                    stance.setdefault(r["repo"], set()).add((f["family"], f["member"]))

    def config_lookup(c: dict, j: float, s: bool) -> str:
        """Apply when a tool the source text prescribes is configured in the target."""
        src, _ = _case_texts(c, clones)
        want = {(f, m) for f, m, p in classify_mentions(src) if p == "prescribe" and not f.startswith("practice_")}
        return "apply" if want & stance.get(c["target"]["repo"], set()) else "abstain"

    thr = args.threshold
    results = [
        score("always_abstain", lambda c, j, s: "abstain"),
        score("always_apply", lambda c, j, s: "apply"),
        score(f"keyword_overlap_jaccard>={thr}", lambda c, j, s: "apply" if j >= thr else "abstain"),
        score("shared_tool_keyword", lambda c, j, s: "apply" if s else "abstain"),
    ]
    if args.mined:
        results.append(score("config_lookup", config_lookup))
    out = {"label_status": "PROPOSED, single annotator, unvalidated", "cases": len(rows),
           "cases_with_unresolved_source_text": sum(not r[3] for r in rows),
           "cases_with_no_target_text": sum(not r[4] for r in rows),
           "label_distribution": {l: sum(r[0]["proposed_label"] == l for r in rows) for l in LABELS},
           "results": results,
           "per_case": [{"case_id": r[0]["case_id"], "jaccard": r[1], "shared_tool_keyword": r[2]} for r in rows]}
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-hash every pointer in a case file against the pinned clones."""
    clones = Path(args.clones_dir)
    bad = total = unhashed = rejected = 0
    for line in Path(args.cases).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if not c.get("format", "").startswith(("transferbench-real-case", "carrybench-real-case")):
            continue
        ptrs = [("source", c["source"]["repo"], c["source"]["sha"], c["source"])]
        ptrs += [("target", c["target"]["repo"], c["target"]["sha"], e) for e in c["target"].get("evidence", [])]
        for role, repo, sha, p in ptrs:
            total += 1
            rp = clone_path(clones, repo)
            if p.get("commit"):  # history pointer (for example a revert commit); needs a deepened clone
                proc = subprocess.run(["git", "-C", str(rp), "cat-file", "-e", p["commit"] + "^{commit}"],
                                      capture_output=True)
                if proc.returncode != 0:
                    bad += 1
                    print(f"MISSING COMMIT {c['case_id']} {role} {repo} {p['commit']}", file=sys.stderr)
                continue
            if not p.get("path"):
                continue
            if p.get("line_start", 0) <= 0:  # presence-only pointer
                names = {t for t, _ in list_tree(rp, sha)}
                ok = p["path"] in names or any(t.startswith(p["path"].rstrip("/") + "/") for t in names)
            else:
                text = read_blob(rp, sha, p["path"])
                ok = text is not None and (p.get("text_sha256") in (None, line_span_hash(text, p["line_start"], p["line_end"])))
                if ok and p.get("text_sha256") is None:  # a null hash accepts any line, so a wrong line number passes
                    unhashed += 1
                    if args.require_hash:
                        rejected += 1
                        print(f"UNHASHED {c['case_id']} {role} {repo} {p.get('path')}:{p.get('line_start')}", file=sys.stderr)
            if not ok:
                bad += 1
                print(f"MISMATCH {c['case_id']} {role} {repo} {p.get('path')}:{p.get('line_start')}", file=sys.stderr)
    print(json.dumps({"pointers": total, "mismatches": bad, "unhashed_line_pointers": unhashed,
                      "unhashed_rejected": rejected}))
    return 1 if bad or rejected else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inventory"); p.add_argument("--repos", required=True)
    p.add_argument("--clones-dir", required=True); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_inventory)
    p = sub.add_parser("mine"); p.add_argument("--repos", required=True)
    p.add_argument("--clones-dir", required=True); p.add_argument("--out", required=True)
    p.add_argument("--include-text", action="store_true",
                   help="LOCAL annotation aid only. Output then contains third-party text and must not be released.")
    p.add_argument("--reverts", type=int, default=0, help="scan this many revert commits (needs history)")
    p.add_argument("--max-files-per-class", type=int, default=400)
    p.add_argument("--no-license-gate", action="store_true")
    p.set_defaults(fn=cmd_mine)
    p = sub.add_parser("pair"); p.add_argument("--mined", required=True); p.add_argument("--out", required=True)
    p.add_argument("--clones-dir", default="")
    p.set_defaults(fn=cmd_pair)
    p = sub.add_parser("baseline"); p.add_argument("--cases", required=True)
    p.add_argument("--clones-dir", required=True); p.add_argument("--threshold", type=float, default=0.15)
    p.add_argument("--mined", default="", help="mined JSONL; enables the config_lookup baseline")
    p.set_defaults(fn=cmd_baseline)
    p = sub.add_parser("verify"); p.add_argument("--cases", required=True)
    p.add_argument("--clones-dir", required=True)
    p.add_argument("--require-hash", action="store_true",
                   help="fail on a pointer that names lines but has no text hash. Use for every new versioned file.")
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
