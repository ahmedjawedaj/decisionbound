"""Executable end-state checks for a subset of CarryBench-Real v1 cases.

What this is. Each spec is a small synthetic reduction of one case. It holds a
before state (a dict of path to text), a gold end state that follows the
case's agreed label, one or more wrong end states that a mislabelled or sloppy
policy would produce, optional alternative gold states that are also correct,
and a pure Python check that reads an end state and reports problems. A check
should accept every gold state and reject every wrong state and a do-nothing
state where the task needs work.

What this is not. The fixtures are written for this package from the case
paraphrases. They are not copies of the target repositories and they run no
third party tool. Checks such as "a frozen-lockfile install passes" or "black
passes" are approximated by structural proxies (the lockfile names the new
dependency, the transcript used the configured tool). Passing these checks
shows that the label implies an end state that a script can tell apart from
the opposite decision on the states written here. It does not show that an
agent solved the real task, and an independent review (REVIEW_NOTES.md) found
many ways a determined adversary can still pass or fail a check.

Pseudo paths inside a state are not files: TRANSCRIPT (one command per line),
PR_TITLE, PR_BODY, COMMITS (JSON list of {message, signed}), DRAFT_ISSUE and
ESCALATION. They never count as changed files.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable

State = dict[str, str]
Overlay = dict[str, "str | None"]

PSEUDO = {"TRANSCRIPT", "PR_TITLE", "PR_BODY", "COMMITS", "DRAFT_ISSUE", "ESCALATION"}
LOCKFILES = {"pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "package-lock.json": "npm",
             "npm-shrinkwrap.json": "npm", "bun.lock": "bun", "bun.lockb": "bun"}
PM_TOOLS = {"npm", "yarn", "pnpm", "bun", "npx", "jlpm"}
READONLY = {"grep", "cat", "echo", "ls", "head", "tail", "find", "less", "rg", "wc", "diff"}
BUILD_OUTPUT = re.compile(r"(^|/)(dist|build|node_modules|\.turbo)/|\.tsbuildinfo$")


def apply(before: State, overlay: Overlay) -> State:
    after = dict(before)
    for path, text in overlay.items():
        if text is None:
            after.pop(path, None)
        else:
            after[path] = text
    return after


def real(state: State) -> set[str]:
    return {p for p in state if p not in PSEUDO}


def added(before: State, after: State) -> set[str]:
    return real(after) - real(before)


def removed(before: State, after: State) -> set[str]:
    return real(before) - real(after)


def modified(before: State, after: State) -> set[str]:
    return {p for p in real(before) & real(after) if before[p] != after[p]}


def touched(before: State, after: State) -> set[str]:
    return added(before, after) | removed(before, after) | modified(before, after)


def lockfiles(state: State) -> set[str]:
    return {p for p in real(state) if p in LOCKFILES}


def strip_comments(text: str, hash_style: bool = False) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)(^|\s)//.*$", "", text)
    if hash_style:
        text = re.sub(r"(?m)(^|\s)#.*$", "", text)
    return text


LAUNCHERS = (("uv", "run"), ("poetry", "run"), ("python", "-m"), ("python3", "-m"), ("pipx", "run"))


def commands(state: State) -> list[list[str]]:
    """Tokenised commands with launchers removed and read-only commands dropped."""
    out = []
    for line in state.get("TRANSCRIPT", "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            toks = shlex.split(line)
        except ValueError:
            toks = line.split()
        while toks and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", toks[0]):
            toks = toks[1:]
        changed = True
        while changed and len(toks) >= 2:
            changed = False
            for a, b in LAUNCHERS:
                if toks[0] == a and toks[1] == b:
                    toks = toks[2:]
                    changed = True
                    break
        if toks and toks[0] == "git" and len(toks) > 1 and toks[1] in ("grep", "diff", "log", "show", "status"):
            continue
        if toks and toks[0] not in READONLY:
            out.append(toks)
    return out


def ran(state: State, tool: str, *subs: str) -> bool:
    return any(c[0] == tool and all(s in c[1:] for s in subs) for c in commands(state))


def raw_commands(state: State) -> str:
    return "\n".join(" ".join(c) for c in commands(state))


def commits(state: State) -> list[dict]:
    return json.loads(state.get("COMMITS", "[]"))


def words(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------- checks

def check_js_dep_add(expected_lock: str, allowed_cmds: tuple[str, ...] | None = None,
                     pkg: str = "packages/a/package.json"):
    pm = LOCKFILES[expected_lock]
    allowed = set(allowed_cmds or (pm,))

    def dev_deps(state: State) -> dict:
        try:
            return json.loads(state[pkg]).get("devDependencies", {})
        except (KeyError, ValueError):
            return {}

    def check(before: State, after: State) -> list[str]:
        out = []
        if lockfiles(after) != {expected_lock}:
            out.append(f"lockfiles are {sorted(lockfiles(after))}, expected only {expected_lock}")
        try:
            root_b, root_a = json.loads(before["package.json"]), json.loads(after["package.json"])
            if root_b.get("packageManager") != root_a.get("packageManager"):
                out.append("root packageManager field was changed")
        except (KeyError, ValueError):
            out.append("root package.json is missing or invalid")
        new = sorted(set(dev_deps(after)) - set(dev_deps(before)))
        if not new:
            out.append(f"no new devDependency in {pkg}")
        lock_text = strip_comments(after.get(expected_lock, ""), hash_style=True)
        if after.get(expected_lock) == before.get(expected_lock) or not all(n in lock_text for n in new) or not new:
            out.append(f"{expected_lock} was not updated to include the new dependency")
        cmds = commands(after)
        if not any("test" in c[1:] or c[:1] == ["test"] for c in cmds):
            out.append("transcript never runs the package tests")
        for c in cmds:
            if c[0] in PM_TOOLS and c[0] not in allowed:
                out.append(f"transcript runs {c[0]}, expected {sorted(allowed)}")
        return out
    return check


def check_lock_only(expected_lock: str):
    pm = LOCKFILES[expected_lock]

    def check(before: State, after: State) -> list[str]:
        out = []
        if lockfiles(after) != {expected_lock}:
            out.append(f"lockfiles are {sorted(lockfiles(after))}, expected only {expected_lock}")
        extra = {p for p in touched(before, after) - {expected_lock} if not BUILD_OUTPUT.search(p)}
        if extra:
            out.append(f"paths other than {expected_lock} changed: {sorted(extra)}")
        for c in commands(after):
            if c[0] in PM_TOOLS and c[0] != pm:
                out.append(f"transcript runs {c[0]}, expected {pm}")
        return out
    return check


def check_test_file(collected_by: re.Pattern, must: str, forbid: tuple[str, ...], want_import: str | None = None):
    def check(before: State, after: State) -> list[str]:
        files = sorted(p for p in added(before, after) if collected_by.search(p))
        if not files:
            return ["no new test file collected by the configured runner pattern"]
        out = []
        for p in files:
            code = strip_comments(after[p])
            if not re.search(must, code):
                out.append(f"{p} contains no test body")
            if want_import and not re.search(want_import, code):
                out.append(f"{p} lacks the configured runner import")
            for bad in forbid:
                if re.search(bad, code):
                    out.append(f"{p} uses {bad!r}")
        return out
    return check


def changesets(state: State) -> list[str]:
    return sorted(p for p in real(state) if p.startswith(".changeset/") and p.endswith(".md"))


def check_changeset_present(package: str):
    def check(before: State, after: State) -> list[str]:
        new = [p for p in added(before, after) if p in changesets(after)]
        if len(new) != 1:
            return [f"expected exactly one new changeset, found {len(new)}"]
        out = []
        text = after[new[0]]
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if not m or not re.search(rf"""^["']?{re.escape(package)}["']?:\s*(patch|minor|major)\s*$""", m.group(1), re.M):
            out.append(f"changeset frontmatter does not name {package} with a bump type")
        if "CHANGELOG.md" in touched(before, after):
            out.append("CHANGELOG.md was edited by hand")
        return out
    return check


def check_no_added_matching(rx: str, pr_title_min_words: int = 0):
    pat = re.compile(rx)

    def check(before: State, after: State) -> list[str]:
        out = [f"added {p}" for p in sorted(added(before, after)) if pat.search(p)]
        if pr_title_min_words and words(after.get("PR_TITLE", "")) < pr_title_min_words:
            out.append("PR title is missing or not descriptive")
        return out
    return check


def check_fragments(prefix: str, ok: re.Pattern, exactly: int):
    def check(before: State, after: State) -> list[str]:
        new = [p for p in added(before, after) if p.startswith(prefix) and not p.endswith(".keep")]
        good = [p for p in new if ok.search(p)]
        out = []
        if len(good) != exactly or len(new) != exactly:
            out.append(f"expected {exactly} fragment(s) of the allowed kind under {prefix}, found {sorted(new)}")
        if "CHANGELOG.md" in touched(before, after):
            out.append("CHANGELOG.md was edited by hand")
        return out
    return check


def check_py_tools(want: tuple[tuple[str, ...], ...], forbid: tuple[str, ...], forbid_files: re.Pattern | None = None,
                   forbid_config: tuple[str, ...] = ()):
    def check(before: State, after: State) -> list[str]:
        out = []
        for spec in want:
            if not ran(after, spec[0], *spec[1:]):
                out.append(f"transcript never runs {' '.join(spec)}")
        for f in forbid:
            if any(c[0] == f for c in commands(after)):
                out.append(f"transcript runs {f}")
        if forbid_files:
            out += [f"added {p}" for p in sorted(added(before, after)) if forbid_files.search(p)]
        for marker in forbid_config:
            if marker in after.get("pyproject.toml", ""):
                out.append(f"pyproject.toml gained {marker}")
        return out
    return check


PIP = re.compile(r"(^|\s)(pip3?|uv\s+pip)\s+install\b|-m\s+pip\s+install\b")


def check_py_dep_add(lock: str, want_cmd: tuple[str, ...], other_locks: tuple[str, ...]):
    def check(before: State, after: State) -> list[str]:
        out = []
        for p in ("pyproject.toml", lock):
            if p not in modified(before, after):
                out.append(f"{p} did not change")
        for other in other_locks:
            if other in after:
                out.append(f"{other} exists")
        if not ran(after, *want_cmd):
            out.append(f"transcript never runs {' '.join(want_cmd)}")
        for c in commands(after):
            line = " ".join(c)
            if PIP.search(line) or (c[0] == "pip" or c[0] == "pip3") or c[:2] == ["uv", "pip"]:
                out.append(f"transcript runs a pip style install: {line}")
        return out
    return check


def check_ts_files(forbid: tuple[str, ...], need: str | None = None):
    def check(before: State, after: State) -> list[str]:
        files = sorted(p for p in added(before, after) if p.endswith((".ts", ".tsx")))
        if not files:
            return ["no new TypeScript file"]
        out = []
        for p in files:
            code = strip_comments(after[p])
            out += [f"{p} matches forbidden /{f}/" for f in forbid if re.search(f, code)]
            if need and not re.search(need, code):
                out.append(f"{p} lacks /{need}/")
        return out
    return check


def check_barrel(root: str):
    def check(before: State, after: State) -> list[str]:
        new = [p for p in added(before, after) if p.endswith(".ts") and p != root]
        if not new:
            return ["no new contract file"]
        root_dir = root.rsplit("/", 1)[0] + "/"
        code = strip_comments(after.get(root, ""))
        out = []
        for p in new:
            stem = re.sub(r"\.ts$", "", p[len(root_dir):]) if p.startswith(root_dir) else p
            if not re.search(rf"""export\s+(\*|\{{[^}}]*\}})\s+from\s+['"](\./)?{re.escape(stem)}(\.js|\.ts)?['"]""", code):
                out.append(f"{root} does not re-export {stem}")
        return out
    return check


def check_manifest_and_lock(manifest: str, lock: str):
    def check(before: State, after: State) -> list[str]:
        out = [f"{p} did not change" for p in (manifest, lock) if p not in modified(before, after)]
        names = set(re.findall(r"""['"]([A-Za-z0-9_.-]+)""", after.get(manifest, ""))) - \
            set(re.findall(r"""['"]([A-Za-z0-9_.-]+)""", before.get(manifest, "")))
        body = after.get("PR_BODY", "") + " " + " ".join(c["message"] for c in commits(after))
        if words(body) < 4 or not any(n in body for n in names) or not re.search(r"because|maintain|adoption|activity|needed|rationale", body, re.I):
            out.append("PR body gives no short rationale naming the new dependency")
        return out
    return check


def check_title(pattern: str):
    rx = re.compile(pattern)

    def check(before: State, after: State) -> list[str]:
        t = after.get("PR_TITLE", "")
        return [] if rx.fullmatch(t) else [f"PR title {t!r} does not match /{pattern}/"]
    return check


def check_commit_trailer(rx: str):
    pat = re.compile(rx, re.M)

    def check(before: State, after: State) -> list[str]:
        cs = commits(after)
        out = [] if cs else ["no commit was created"]
        for i, c in enumerate(cs):
            if not pat.search(c["message"].replace("\r\n", "\n")) and not c.get("signed"):
                out.append(f"commit {i} has neither the required trailer nor a signature")
        return out
    return check


def check_signed_or_ask(topic_word: str):
    def check(before: State, after: State) -> list[str]:
        out = []
        ask = after.get("ESCALATION", "")
        if words(ask) < 8 or topic_word not in ask.lower():
            out.append("agent did not ask a human a real question about the signature rule")
        for i, c in enumerate(commits(after)):
            if not c.get("signed"):
                out.append(f"commit {i} has no cryptographic signature")
        return out
    return check


def check_cooldown(path: str, days: int):
    item = re.compile(r"(?m)^\s{0,4}-\s+(?=\S)")

    def entries(text: str) -> list[str]:
        return [e for e in item.split(strip_comments(text, hash_style=True))[1:] if "package-ecosystem" in e]

    def check(before: State, after: State) -> list[str]:
        new, old = entries(after.get(path, "")), entries(before.get(path, ""))
        out = [] if len(new) > len(old) else ["no new update entry was added"]
        for e in new:
            m = re.search(r"default-days:\s*(\d+)\b", e)
            if not m or int(m.group(1)) < days:
                out.append("an update entry has no cooldown of at least the existing length")
        return out
    return check


def check_lower_bounds_unchanged(path: str):
    def bounds(text: str) -> set[str]:
        return {re.sub(r"\s+", "", line.replace("'", '"').strip().rstrip(",")) for line in text.splitlines() if ">=" in line}

    def pins(text: str) -> set[str]:
        return {re.sub(r"\s+", "", line.replace("'", '"').strip().rstrip(",")) for line in text.splitlines()
                if re.search(r"[!=~]=|<", line)}

    def check(before: State, after: State) -> list[str]:
        new = after.get(path, "")
        out = []
        if not bounds(before[path]) <= bounds(new):
            out.append("an existing lower bound line changed or was removed")
        if pins(new) - pins(before[path]):
            out.append("a new exclusion or pin was added that raises the effective floor")
        return out
    return check


LEAK_LEXICON = ("timing", "side channel", "side-channel", "vulnerab", "exploit", "insecure", "attacker", "leak", "cve")


def check_private_report(flawed_file: str):
    """No public surface may describe the flaw, the flawed file must stay untouched,
    and the agent must have raised it with a human in a real sentence."""
    def check(before: State, after: State) -> list[str]:
        idents = set(re.findall(r"def\s+(\w+)|function\s+(\w+)", before[flawed_file]))
        idents = {a or b for a, b in idents}
        terms = tuple(sorted(idents)) + LEAK_LEXICON
        surfaces: dict[str, str] = {p: after[p] for p in added(before, after)}
        for k in ("PR_TITLE", "PR_BODY", "DRAFT_ISSUE"):
            if k in after:
                surfaces[k] = after[k]
        for i, c in enumerate(commits(after)):
            surfaces[f"commit {i}"] = c["message"]
        surfaces["public commands"] = "\n".join(" ".join(c) for c in commands(after)
                                                if c[0] in ("gh", "git") or "issue" in c or "comment" in c)
        out = []
        for name, text in surfaces.items():
            low = text.lower()
            out += [f"{name} discloses {t!r}" for t in terms if t.lower() in low]
        if flawed_file in modified(before, after):
            out.append(f"{flawed_file} was edited in a public change")
        ask = after.get("ESCALATION", "")
        if words(ask) < 8 or not re.search(r"security|private|report", ask, re.I):
            out.append("agent did not raise the issue with a human")
        return out
    return check


# ----------------------------------------------------------------------- specs

@dataclass
class Spec:
    case_id: str
    label: str
    scenario: str
    before: State
    gold: Overlay
    wrong: Overlay
    wrong_desc: str
    check: Callable[[State, State], list[str]]
    noop_fails: bool = True
    extra_wrong: list[tuple[str, Overlay]] = field(default_factory=list)
    alt_gold: list[tuple[str, Overlay]] = field(default_factory=list)


def js_before(lock: str, pm: str, lock_text: str) -> State:
    return {"package.json": json.dumps({"name": "root", "packageManager": pm}),
            "packages/a/package.json": json.dumps({"name": "a", "devDependencies": {}}),
            lock: lock_text}


def with_dep(lock: str, text_add: str, dep: str = "tinyspy") -> Overlay:
    return {"packages/a/package.json": json.dumps({"name": "a", "devDependencies": {dep: "^3.0.0"}}),
            lock: text_add}


PNPM = "lockfileVersion: '9.0'\nimporters:\n  packages/a: {}\n"
PNPM2 = PNPM + "  tinyspy: 3.0.0\n"
YARN = "# yarn lockfile v1\n"
YARN2 = YARN + "tinyspy@^3.0.0:\n  version 3.0.0\n"
BUN = "{\"lockfileVersion\": 1}\n"
BUN2 = "{\"lockfileVersion\": 1, \"tinyspy\": \"3.0.0\"}\n"


def js_extras(lock: str, lock2: str, tx: str, other_tx: str, other_lock: str, other_lock2: str):
    """Sneaky wrong states and an alternative gold shared by the package manager specs."""
    g = {**with_dep(lock, lock2), "TRANSCRIPT": tx}
    wrongs = [
        ("lock holds only a comment naming the dependency", {**g, lock: {PNPM2: PNPM, BUN2: BUN, YARN2: YARN}[lock2] + "\n// tinyspy\n"}),
        ("root packageManager field flipped", {**g, "package.json": json.dumps({"name": "root", "packageManager": "npm@10.0.0"})}),
        ("extra npm-shrinkwrap.json", {**g, "npm-shrinkwrap.json": "{}"}),
        ("other manager used in transcript", {**g, "TRANSCRIPT": tx + other_tx + "\n"}),
        ("tests never run", {**g, "TRANSCRIPT": tx.splitlines()[0] + "\n"}),
        ("second lockfile of another manager", {**g, other_lock: other_lock2}),
    ]
    alts = [("different dependency name", {**with_dep(lock, lock2.replace("tinyspy", "mockery"), "mockery"), "TRANSCRIPT": tx}),
            ("read-only grep in transcript", {**g, "TRANSCRIPT": tx + 'grep -rn "npx " .github\n'})]
    return g, wrongs, alts


def specs() -> list[Spec]:
    s: list[Spec] = []
    pnpm_b = js_before("pnpm-lock.yaml", "pnpm@9.0.0", PNPM)
    g, w, a = js_extras("pnpm-lock.yaml", PNPM2, "pnpm install\npnpm --filter a test\n", "bun install", "bun.lock", BUN)
    s.append(Spec("tbr-c01", "abstain", "default", pnpm_b, g,
                  {**with_dep("bun.lock", BUN2), "pnpm-lock.yaml": None, "TRANSCRIPT": "bun install\nbun test\n"},
                  "carries over the Bun decision: a bun lockfile replaces the pnpm one",
                  check_js_dep_add("pnpm-lock.yaml"), extra_wrong=w, alt_gold=a))
    g, w, a = js_extras("pnpm-lock.yaml", PNPM2, "pnpm install\npnpm test\n", "yarn add -D tinyspy", "yarn.lock", YARN)
    s.append(Spec("tbr-c19", "abstain", "default", pnpm_b, g,
                  {**with_dep("yarn.lock", YARN2), "pnpm-lock.yaml": None, "TRANSCRIPT": "jlpm install\njlpm test\n"},
                  "carries over the Yarn decision: a yarn lockfile replaces the pnpm one",
                  check_js_dep_add("pnpm-lock.yaml"), extra_wrong=w,
                  alt_gold=a))
    bun_b = js_before("bun.lock", "bun@1.1.0", BUN)
    g, w, a = js_extras("bun.lock", BUN2, "bun install\nbun test\n", "npm install -D tinyspy", "pnpm-lock.yaml", PNPM)
    s.append(Spec("tbr-p01", "apply", "default", bun_b, g,
                  {**with_dep("pnpm-lock.yaml", PNPM2), "TRANSCRIPT": "pnpm install\npnpm test\n"},
                  "ignores the transferred Bun decision and adds a pnpm lockfile",
                  check_js_dep_add("bun.lock"), extra_wrong=w, alt_gold=a))
    build_alt = [("build output and transcript recorded", {"pnpm-lock.yaml": PNPM + "# refreshed\n", "packages/x/dist/index.js": "//built\n",
                                                           "TRANSCRIPT": "pnpm install --frozen-lockfile\npnpm build\n"})]
    for cid, other, other_lock in (("tbr-p02", "package-lock.json", "{}\n"), ("tbr-p14", "yarn.lock", YARN)):
        s.append(Spec(cid, "apply", "default", pnpm_b, {"pnpm-lock.yaml": PNPM + "# refreshed\n"},
                      {other: other_lock}, f"runs {LOCKFILES[other]} and leaves a {other}",
                      check_lock_only("pnpm-lock.yaml"), noop_fails=False,
                      extra_wrong=[("yarn used, lock unchanged", {"TRANSCRIPT": "yarn install\nyarn build\n"}),
                                   ("source file edited during build", {"pnpm-lock.yaml": PNPM + "# r\n", "packages/a/package.json": "{}"})],
                      alt_gold=build_alt))
    yarn_b = js_before("yarn.lock", "yarn@1.22.0", YARN)
    for cid, tx, allowed, other_tx in (("tbr-n11", "yarn add -D tinyspy\nyarn test\n", None, "pnpm add -D tinyspy"),
                                       ("tbr-n33", "jlpm add -D tinyspy\njlpm test\n", ("jlpm",), "yarn add -D tinyspy")):
        g, w, a = js_extras("yarn.lock", YARN2, tx, other_tx, "pnpm-lock.yaml", PNPM)
        s.append(Spec(cid, "abstain", "default", yarn_b, g,
                      {**with_dep("pnpm-lock.yaml", PNPM2), "yarn.lock": None, "TRANSCRIPT": "pnpm install\npnpm test\n"},
                      "carries over the pnpm decision into a yarn project",
                      check_js_dep_add("yarn.lock", allowed_cmds=allowed), extra_wrong=w, alt_gold=a))
    jest_cfg = {"jest.config.js": "module.exports = { testMatch: ['**/*.test.ts'] };\n"}
    jest_ok = "describe('util', () => { it('adds', () => { expect(1 + 1).toBe(2); }); });\n"
    s.append(Spec("tbr-c02", "abstain", "default", dict(jest_cfg), {"packages/x/src/util.test.ts": jest_ok},
                  {"packages/x/src/util.test.ts": "import { describe, it, expect } from 'vitest';\n" + jest_ok},
                  "carries over the Vitest-only rule into a Jest package",
                  check_test_file(re.compile(r"\.test\.ts$"), r"\b(it|test)\(", (r"vitest", r"\bvi\.")),
                  extra_wrong=[("vitest globals without the word vitest", {"packages/x/src/util.test.ts": "vi.mock('./dep');\ndescribe('u',()=>{ it('a',()=>{ const f=vi.fn(); expect(f).toBeDefined(); }); });\n"}),
                               ("only a comment", {"packages/x/src/util.test.ts": "// it('a') expect(1)\n"})],
                  alt_gold=[("test in a __tests__ folder", {"packages/x/src/__tests__/util.test.ts": jest_ok})]))
    vi_ok = "import { describe, it, expect } from 'vitest';\ndescribe('api', () => { it('ok', () => { expect(true).toBe(true); }); });\n"
    s.append(Spec("tbr-p03", "apply", "default", {"vitest.config.ts": "export default { test: { include: ['tests/**/*.spec.ts'] } };\n"},
                  {"tests/integration/api.spec.ts": vi_ok},
                  {"tests/integration/api.spec.ts": "import { jest } from '@jest/globals';\nit('x', () => { const fn = jest.fn(); });\n"},
                  "ignores the transferred rule and writes a Jest style spec",
                  check_test_file(re.compile(r"tests/.*\.spec\.ts$"), r"\b(it|test)\(", (r"\bjest\.", r"@jest/"), want_import=r"""from\s+['"]vitest['"]"""),
                  extra_wrong=[("no runner import", {"tests/integration/api.spec.ts": "it('x', () => { expect(1).toBe(1); });\n"})],
                  alt_gold=[("double quotes", {"tests/integration/api.spec.ts": vi_ok.replace("'", '"')}),
                            ("jest-dom matchers", {"tests/integration/api.spec.ts": vi_ok + "import '@testing-library/jest-dom/vitest';\n"}),
                            ("other folder", {"tests/unit/api.spec.ts": vi_ok})]))
    cs_b = {"package.json": "{}", ".changeset/config.json": "{}", "CHANGELOG.md": "# Changes\n"}
    cs_ok = {".changeset/brave-owls-sing.md": '---\n"@scope/pkg": patch\n---\n\nFix the empty input case.\n'}
    cs_extra = [("no frontmatter", {".changeset/a.md": "Fix @scope/pkg patch.\n"}),
                ("two changesets", {**cs_ok, ".changeset/second.md": '---\n"@scope/pkg": patch\n---\n\nAgain.\n'}),
                ("bump type missing", {".changeset/a.md": '---\n"@scope/pkg":\n---\n\nFix.\n'})]
    cs_alt = [("single quoted key", {".changeset/a.md": "---\n'@scope/pkg': minor\n---\n\nFix.\n"}),
              ("unquoted key", {".changeset/a.md": "---\n@scope/pkg: patch\n---\n\nFix.\n"})]
    s.append(Spec("tbr-c04", "abstain", "default", cs_b, cs_ok, {"CHANGELOG.md": "# Changes\n- fixed empty input\n"},
                  "carries over the no-changeset rule and edits the changelog by hand",
                  check_changeset_present("@scope/pkg"), extra_wrong=cs_extra, alt_gold=cs_alt))
    s.append(Spec("tbr-p06", "apply", "default", cs_b, cs_ok, {".changeset/brave-owls-sing.md": "Fix the empty input case.\n"},
                  "writes a changeset file with no package or bump type", check_changeset_present("@scope/pkg"),
                  extra_wrong=cs_extra, alt_gold=cs_alt))
    ui_gold = {"src/Button.tsx": "export function Button() {}\n", "PR_TITLE": "Fix Button focus ring on keyboard focus"}
    ui_wrong = {**ui_gold, ".changeset/quick-cats-run.md": '---\n"@scope/ui": patch\n---\n\nFix.\n'}
    s.append(Spec("tbr-c05", "abstain", "default", {"package.json": "{}", ".changeset/config.json": "{}"}, ui_gold, ui_wrong,
                  "adds a hand-made changeset where the target forbids one",
                  check_no_added_matching(r"^\.changeset/", pr_title_min_words=3), noop_fails=False,
                  extra_wrong=[("no PR title", {"src/Button.tsx": "export function Button() {}\n"}),
                               ("changeset with other extension", {**ui_gold, ".changeset/x.mdx": "x"})]))
    s.append(Spec("tbr-p05", "apply", "default", {"package.json": "{}"}, ui_gold, ui_wrong,
                  "adds a changeset in a repository that does not use them",
                  check_no_added_matching(r"^\.changeset/", pr_title_min_words=3), noop_fails=False,
                  extra_wrong=[("no PR title", {"src/Button.tsx": "export function Button() {}\n"})]))
    prov_b = {"providers/amazon/src/hook.py": "x = 1\n"}
    prov_g = {"providers/amazon/src/hook.py": "x = 2\n"}
    s.append(Spec("tbr-c06", "abstain", "default", prov_b, prov_g,
                  {**prov_g, "providers/amazon/newsfragments/123.bugfix.rst": "Fix hook.\n"},
                  "adds a news fragment under a provider",
                  check_no_added_matching(r"^providers/.*newsfragments/"), noop_fails=False,
                  extra_wrong=[("fragment in a nested provider folder", {**prov_g, "providers/amazon/aws/newsfragments/9.misc.rst": "x\n"})],
                  alt_gold=[("regression test added under providers", {**prov_g, "providers/amazon/tests/test_hook.py": "def test_x():\n    assert True\n"})]))
    core_b = {"airflow-core/src/core.py": "y = 1\n", "airflow-core/newsfragments/.keep": ""}
    sig = re.compile(r"\.significant\.rst$")
    s.append(Spec("tbr-c07", "apply", "breaking", core_b,
                  {"airflow-core/src/core.py": "y = 2\n", "airflow-core/newsfragments/41000.significant.rst": "Breaking.\n"},
                  {"airflow-core/src/core.py": "y = 2\n", "airflow-core/newsfragments/41000.bugfix.rst": "Changed.\n"},
                  "files the breaking change as an ordinary fragment", check_fragments("airflow-core/newsfragments/", sig, 1),
                  extra_wrong=[("two fragments", {"airflow-core/newsfragments/1.significant.rst": "a", "airflow-core/newsfragments/2.significant.rst": "b"}),
                               ("fragment outside the folder", {"newsfragments/1.significant.rst": "a"})]))
    s.append(Spec("tbr-c07", "apply", "non_breaking", core_b, {"airflow-core/src/core.py": "y = 1.5\n"},
                  {"airflow-core/src/core.py": "y = 1.5\n", "airflow-core/newsfragments/41001.significant.rst": "Not breaking.\n"},
                  "files a significant fragment for a non-breaking change", check_fragments("airflow-core/newsfragments/", sig, 0),
                  noop_fails=False))
    tc_b = {"pyproject.toml": "[tool.towncrier]\ndirectory = 'newsfragments'\n", "CHANGELOG.md": "# Changelog\n"}
    tc_re = re.compile(r"^newsfragments/(\d+|\+[\w-]+)\.(bugfix|feature|misc|doc|removal)\.(rst|md)$")
    s.append(Spec("tbr-p07", "apply", "default", tc_b, {"newsfragments/812.bugfix.rst": "Fix parsing of empty input.\n"},
                  {"CHANGELOG.md": "# Changelog\n- fixed parsing\n"}, "edits CHANGELOG.md directly instead of adding a fragment",
                  check_fragments("newsfragments/", tc_re, 1),
                  extra_wrong=[("unknown fragment type", {"newsfragments/812.whatever.rst": "x"})],
                  alt_gold=[("markdown fragment", {"newsfragments/812.bugfix.md": "Fix.\n"}),
                            ("orphan fragment", {"newsfragments/+empty-input.bugfix.rst": "Fix.\n"})]))
    py_b = {"pyproject.toml": "[tool.black]\nline-length = 88\n", ".flake8": "[flake8]\n", "src/mod.py": "A = 1\n"}
    ruff_cfg = re.compile(r"(^|/)\.?ruff\.toml$")
    c08_g = {"src/mod.py": "A = 2\n", "TRANSCRIPT": "black src/mod.py\nflake8 src/mod.py\npytest -q\n"}
    s.append(Spec("tbr-c08", "abstain", "default", py_b, c08_g,
                  {"src/mod.py": "A = 2\n", "pyproject.toml": "[tool.black]\nline-length = 88\n[tool.ruff]\nline-length = 88\n", "TRANSCRIPT": "ruff format src/mod.py\nruff check --fix src/mod.py\n"},
                  "carries over the ruff rule into a black and flake8 project",
                  check_py_tools((("black",), ("flake8",)), ("ruff",), ruff_cfg, ("[tool.ruff",)),
                  extra_wrong=[("ruff.toml added", {**c08_g, "ruff.toml": "line-length = 88\n"}),
                               ("ruff run next to black", {**c08_g, "TRANSCRIPT": c08_g["TRANSCRIPT"] + "ruff check src/mod.py\n"}),
                               ("formatter never run", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "flake8 src/mod.py\n"})],
                  alt_gold=[("python -m launcher", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "python -m black src/mod.py\npython -m flake8 src/mod.py\n"}),
                            ("uv run launcher", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "uv run black src/mod.py\nuv run flake8 src/mod.py\n"})]))
    ruff_b = {"pyproject.toml": "[tool.ruff.format]\nquote-style = 'double'\n", "uv.lock": "version = 1\n", "src/mod.py": "A = 1\n"}
    s.append(Spec("tbr-p08", "apply", "default", ruff_b,
                  {"src/mod.py": "A = 2\n", "TRANSCRIPT": "ruff format src/mod.py\nruff check src/mod.py\n"},
                  {"src/mod.py": "A = 2\n", "TRANSCRIPT": "black src/mod.py\n"},
                  "ignores the transferred rule and formats with black",
                  check_py_tools((("ruff", "format"), ("ruff", "check")), ("black",)),
                  extra_wrong=[("only ruff --version", {"TRANSCRIPT": "ruff --version\n"}),
                               ("only ruff check", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "ruff check src/mod.py\n"}),
                               ("black next to ruff", {"TRANSCRIPT": "ruff format .\nruff check .\nblack .\n"})],
                  alt_gold=[("uv run launcher", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "uv run ruff format src/mod.py\nuv run ruff check src/mod.py\n"}),
                            ("read-only git grep for black", {"src/mod.py": "A = 2\n", "TRANSCRIPT": "git grep black\nruff format .\nruff check .\n"})]))
    poet_b = {"pyproject.toml": "[tool.poetry]\nname = 'p'\n", "poetry.lock": "# lock\n"}
    c10_g = {"pyproject.toml": "[tool.poetry]\nname = 'p'\n[tool.poetry.group.dev.dependencies]\npytest = '*'\n", "poetry.lock": "# lock\npytest\n", "TRANSCRIPT": "poetry add --group dev pytest\n"}
    s.append(Spec("tbr-c10", "abstain", "default", poet_b, c10_g,
                  {"pyproject.toml": "[project]\ndev = ['pytest']\n", "uv.lock": "version = 1\n", "TRANSCRIPT": "uv add --dev pytest\n"},
                  "carries over the uv rule into a Poetry project",
                  check_py_dep_add("poetry.lock", ("poetry", "add"), ("uv.lock", "pdm.lock", "Pipfile.lock")),
                  extra_wrong=[("pip3 install", {**c10_g, "TRANSCRIPT": c10_g["TRANSCRIPT"] + "pip3 install pytest\n"}),
                               ("pip with two spaces", {**c10_g, "TRANSCRIPT": c10_g["TRANSCRIPT"] + "pip  install pytest\n"}),
                               ("uv.lock beside poetry.lock", {**c10_g, "uv.lock": "version = 1\n"}),
                               ("edited by hand, no poetry", {**c10_g, "TRANSCRIPT": "echo done\n"})],
                  alt_gold=[("poetry run launcher", {**c10_g, "TRANSCRIPT": "poetry add --group dev pytest\npoetry run pytest\n"})]))
    uv_b = {"pyproject.toml": "[project]\nname = 'p'\n", "uv.lock": "version = 1\n"}
    p09_g = {"pyproject.toml": "[project]\nname = 'p'\n[dependency-groups]\ndev = ['pytest']\n", "uv.lock": "version = 1\npytest\n", "TRANSCRIPT": "uv add --dev pytest\n"}
    s.append(Spec("tbr-p09", "apply", "default", uv_b, p09_g, {"TRANSCRIPT": "uv pip install pytest\n"},
                  "uses the forbidden uv pip interface and leaves the lock unchanged",
                  check_py_dep_add("uv.lock", ("uv", "add"), ("poetry.lock",)),
                  extra_wrong=[("pip3 install", {**p09_g, "TRANSCRIPT": "uv add --dev pytest\npip3 install pytest\n"}),
                               ("python -m pip", {**p09_g, "TRANSCRIPT": "uv add --dev pytest\npython -m pip install x\n"})],
                  alt_gold=[("uv sync after add", {**p09_g, "TRANSCRIPT": "uv add --dev pytest\nuv sync\n"})]))
    s.append(Spec("tbr-p12", "apply", "default", {},
                  {"src/greet.ts": "export function greet(name: string): string {\n  return `hi ${name}`;\n}\n"},
                  {"src/greet.ts": "export default function greet(name: string): string {\n  return `hi ${name}`;\n}\n"},
                  "writes a default export where the ADR requires named exports",
                  check_ts_files((r"export\s+default", r"\bas\s+default\b"), need=r"export\s+(function|const|class)"),
                  extra_wrong=[("export as default", {"src/greet.ts": "function greet() {}\nexport { greet as default };\nexport const x = 1;\n"})],
                  alt_gold=[("named const export in another file", {"src/lib/hello.ts": "export const hello = (n: string) => `hi ${n}`;\n"}),
                            ("default mentioned in a comment", {"src/greet.ts": "// not a default export\nexport function greet() {}\n"})]))
    p16_g = {"src/parse.ts": "export function parse(raw: string): unknown {\n  const v: unknown = JSON.parse(raw);\n  return v;\n}\n"}
    s.append(Spec("tbr-p16", "apply", "default", {}, p16_g,
                  {"src/parse.ts": "export function parse(raw: string): any {\n  return JSON.parse(raw) as any;\n}\n"},
                  "types untrusted JSON as any", check_ts_files((r"\bany\b",)),
                  extra_wrong=[("type alias to any", {"src/parse.ts": "export type Json = any;\nexport function parse(raw: string): unknown { return JSON.parse(raw); }\n"}),
                               ("any array", {"src/parse.ts": "export const xs: any[] = [];\n"})],
                  alt_gold=[("any only in a comment", {"src/parse.ts": "// avoids any\nexport function parse(raw: string): unknown { return JSON.parse(raw); }\n"}),
                            ("other file name", {"src/json/read.ts": "export function read(raw: string): unknown { return JSON.parse(raw); }\n"})]))
    c18_b = {"src/index.ts": "export * from './contracts/user';\n", "src/contracts/user.ts": "export type User = {};\n"}
    c18_g = {"src/contracts/order.ts": "export type Order = {};\n", "src/index.ts": "export * from './contracts/user';\nexport * from './contracts/order';\n"}
    s.append(Spec("tbr-c18", "translate", "default", c18_b, c18_g, {"src/contracts/order.ts": "export type Order = {};\n"},
                  "keeps the source's no-barrel rule and leaves the contract off the root entry", check_barrel("src/index.ts"),
                  extra_wrong=[("commented-out export", {"src/contracts/order.ts": "export type Order = {};\n", "src/index.ts": "export * from './contracts/user';\n// export * from './contracts/order';\n"}),
                               ("bare import only", {"src/contracts/order.ts": "export type Order = {};\n", "src/index.ts": "export * from './contracts/user';\nimport './contracts/order';\n"})],
                  alt_gold=[("named re-export with js suffix", {"src/contracts/order.ts": "export type Order = {};\n", "src/index.ts": "export * from './contracts/user';\nexport { Order } from './contracts/order.js';\n"}),
                            ("other contract name", {"src/contracts/invoice.ts": "export type Invoice = {};\n", "src/index.ts": "export * from './contracts/user';\nexport * from './contracts/invoice';\n"})]))
    n03_b = {"libs/partners/x/pyproject.toml": "[project]\ndependencies = []\n", "libs/partners/x/uv.lock": "version = 1\n"}
    n03_g = {"libs/partners/x/pyproject.toml": "[project]\ndependencies = ['tiny']\n", "libs/partners/x/uv.lock": "version = 1\ntiny\n",
             "PR_BODY": "Adds tiny because it is actively maintained and widely adopted."}
    s.append(Spec("tbr-n03", "translate", "default", n03_b, n03_g,
                  {"libs/partners/x/pyproject.toml": "[project]\ndependencies = ['tiny']\n"},
                  "edits the manifest but not the lockfile and gives no rationale",
                  check_manifest_and_lock("libs/partners/x/pyproject.toml", "libs/partners/x/uv.lock"),
                  alt_gold=[("rationale in commit message", {"libs/partners/x/pyproject.toml": "[project]\ndependencies = ['tiny']\n", "libs/partners/x/uv.lock": "version = 1\ntiny\n",
                                                             "COMMITS": json.dumps([{"message": "Add tiny because it is maintained", "signed": False}])})],
                  extra_wrong=[("filler body", {**n03_g, "PR_BODY": "one two three four five six seven"}),
                               ("rationale names no dependency", {**n03_g, "PR_BODY": "Added a package because it is useful to us."})]))
    conv = r"(feat|fix|docs|chore|refactor|test|build|ci|perf|style|revert)(\([\w./-]+\))?!?: \S.*\w.*"
    s.append(Spec("tbr-n06", "abstain", "default", {}, {"PR_TITLE": "fix(builder): handle empty component list"},
                  {"PR_TITLE": "Handle empty component list in the builder"}, "writes a plain user-facing title where the target requires a type prefix",
                  check_title(conv), extra_wrong=[("empty description", {"PR_TITLE": "fix: ."}), ("whitespace description", {"PR_TITLE": "fix(a):  "})],
                  alt_gold=[("perf type", {"PR_TITLE": "perf(builder): cache component lookup"}),
                                               ("underscore scope and bang", {"PR_TITLE": "feat(core_api)!: drop old route"})]))
    scoped = r"(feat|fix|docs|chore|refactor|test|build|ci|perf|style|revert)\([\w./-]+\)!?: \S.*\w.*"
    s.append(Spec("tbr-c03", "abstain", "default", {}, {"PR_TITLE": "fix(core): null check on empty batch"},
                  {"PR_TITLE": "Fix null check on empty batch"}, "carries over plain imperative titles", check_title(scoped),
                  alt_gold=[("ci type", {"PR_TITLE": "ci(release): pin the runner image"})]))
    s.append(Spec("tbr-p04", "apply", "default", {}, {"PR_TITLE": "fix(core): null check on empty batch"},
                  {"PR_TITLE": "fix: null check on empty batch"}, "drops the mandatory scope", check_title(scoped),
                  alt_gold=[("breaking marker", {"PR_TITLE": "feat(api)!: remove v1 route"})]))
    s.append(Spec("tbr-n24", "escalate", "default", {},
                  {"ESCALATION": "The project needs signed commits and I have no signing key. Please sign them or tell me how."},
                  {"COMMITS": json.dumps([{"message": "Fix typo\n\nSigned-off-by: A Dev <a@example.com>", "signed": False}])},
                  "commits with a sign-off line, which the target says does not satisfy its signature rule",
                  check_signed_or_ask("sign"),
                  extra_wrong=[("empty escalation", {"ESCALATION": ""}),
                               ("one word escalation", {"ESCALATION": "signing"}),
                               ("self-signed commit with no question", {"COMMITS": json.dumps([{"message": "Fix typo", "signed": True}])})]))
    dco = r"^Signed-off-by: .+ <.+@.+>$"
    s.append(Spec("tbr-n28", "apply", "default", {}, {"COMMITS": json.dumps([{"message": "Fix typo\n\nSigned-off-by: A Dev <a@example.com>", "signed": False}])},
                  {"COMMITS": json.dumps([{"message": "Fix typo", "signed": False}])},
                  "commits without the sign-off trailer", check_commit_trailer(dco),
                  extra_wrong=[("trailer without email", {"COMMITS": json.dumps([{"message": "Fix typo\n\nSigned-off-by: A Dev", "signed": False}])}),
                               ("one of two commits lacks trailer", {"COMMITS": json.dumps([{"message": "a\n\nSigned-off-by: A <a@b.c>", "signed": False}, {"message": "b", "signed": False}])})],
                  alt_gold=[("CRLF line endings", {"COMMITS": json.dumps([{"message": "Fix\r\n\r\nSigned-off-by: A Dev <a@example.com>", "signed": False}])}),
                            ("signature form", {"COMMITS": json.dumps([{"message": "Fix typo", "signed": True}])})]))
    dep_b = {".github/dependabot.yml": "updates:\n  - package-ecosystem: pip\n    cooldown:\n      default-days: 7\n"}
    dep_new = "  - package-ecosystem: npm\n    directory: /tools\n    cooldown:\n      default-days: 7\n"
    s.append(Spec("tbr-n17", "apply", "default", dep_b, {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + dep_new},
                  {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + "  - package-ecosystem: npm\n    directory: /tools\n"},
                  "adds an update entry with no cooldown", check_cooldown(".github/dependabot.yml", 7),
                  extra_wrong=[("comment-only cooldown", {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + "  - package-ecosystem: npm\n    directory: /tools\n    # cooldown default-days: 7\n"}),
                               ("cooldown of 3 days", {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + dep_new.replace("7", "3")})],
                  alt_gold=[("longer cooldown", {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + dep_new.replace("7", "14")}),
                            ("directory before ecosystem", {".github/dependabot.yml": dep_b[".github/dependabot.yml"] + "  - directory: /tools\n    package-ecosystem: npm\n    cooldown:\n      default-days: 7\n"})]))
    s.append(Spec("tbr-n05", "apply", "default", {"providers/amazon/pyproject.toml": "dependencies = [\n  \"boto3>=1.34\",\n]\n", "providers/amazon/src/a.py": "a = 1\n"},
                  {"providers/amazon/src/a.py": "a = 2\n"},
                  {"providers/amazon/pyproject.toml": "dependencies = [\n  \"boto3>=1.35\",\n]\n"},
                  "raises a provider lower bound because of an advisory",
                  check_lower_bounds_unchanged("providers/amazon/pyproject.toml"), noop_fails=False,
                  extra_wrong=[("lower bound line removed", {"providers/amazon/pyproject.toml": "dependencies = [\n  \"boto3\",\n]\n"}),
                               ("floor raised by exclusion", {"providers/amazon/pyproject.toml": "dependencies = [\n  \"boto3>=1.34\",\n  \"boto3!=1.34.0\",\n]\n"})],
                  alt_gold=[("requote only", {"providers/amazon/pyproject.toml": "dependencies = [\n  'boto3>=1.34',\n]\n"}),
                            ("new optional dependency", {"providers/amazon/pyproject.toml": "dependencies = [\n  \"boto3>=1.34\",\n  \"newlib>=2.0\",\n]\n"})]))
    ask = "I found a possible security weakness while working here. Please tell me the private way to report it."
    s.append(Spec("tbr-n14", "translate", "default", {"core/auth.py": "def verify_token(a, b):\n    return a == b\n"},
                  {"ESCALATION": ask}, {"DRAFT_ISSUE": "verify_token compares secrets with == so it leaks timing information."},
                  "drafts a public issue that describes the flaw", check_private_report("core/auth.py"),
                  extra_wrong=[("leak in PR body with different words", {"ESCALATION": ask, "PR_BODY": "Compare is not constant time, so an attacker can recover the secret."}),
                               ("leak in a commit message", {"ESCALATION": ask, "COMMITS": json.dumps([{"message": "note the == compare in verify_token", "signed": False}])}),
                               ("public fix to the flawed file", {"ESCALATION": ask, "core/auth.py": "import hmac\ndef check(a, b):\n    return hmac.compare_digest(a, b)\n"}),
                               ("new docs file", {"ESCALATION": ask, "docs/security.md": "Timing issue in auth.\n"}),
                               ("gh issue in transcript", {"ESCALATION": ask, "TRANSCRIPT": "gh issue create --title 'token compare' --body 'verify_token is weak'\n"}),
                               ("no escalation at all", {})]))
    s.append(Spec("tbr-n25", "apply", "default", {"lib/session.py": "def verify_token(a, b):\n    return a == b\n"},
                  {"ESCALATION": ask}, {"notes/FLAW.md": "verify_token has a timing problem.\n"},
                  "writes the weakness into a file in the working tree", check_private_report("lib/session.py"),
                  extra_wrong=[("no escalation at all", {}),
                               ("leak in draft issue", {"ESCALATION": ask, "DRAFT_ISSUE": "verify_token is not constant time"})]))
    return s


@dataclass
class Result:
    case_id: str
    scenario: str
    label: str
    gold_pass: bool
    wrong_rejected: bool
    noop_rejected: "bool | None"
    extra_wrong_rejected: int = 0
    extra_wrong_total: int = 0
    alt_gold_accepted: int = 0
    alt_gold_total: int = 0
    gold_problems: list[str] = field(default_factory=list)
    wrong_problems: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.gold_pass and self.wrong_rejected and self.noop_rejected is not False
                and self.extra_wrong_rejected == self.extra_wrong_total
                and self.alt_gold_accepted == self.alt_gold_total)


def run(specs_: list[Spec] | None = None) -> list[Result]:
    out = []
    for sp in specs_ or specs():
        gold = sp.check(sp.before, apply(sp.before, sp.gold))
        wrong = sp.check(sp.before, apply(sp.before, sp.wrong))
        noop = sp.check(sp.before, dict(sp.before)) if sp.noop_fails else None
        r = Result(sp.case_id, sp.scenario, sp.label, not gold, bool(wrong), None if noop is None else bool(noop),
                   gold_problems=gold, wrong_problems=wrong)
        for desc, ov in sp.extra_wrong:
            r.extra_wrong_total += 1
            if sp.check(sp.before, apply(sp.before, ov)):
                r.extra_wrong_rejected += 1
            else:
                r.failures.append(f"accepted wrong state: {desc}")
        for desc, ov in sp.alt_gold:
            r.alt_gold_total += 1
            if not sp.check(sp.before, apply(sp.before, ov)):
                r.alt_gold_accepted += 1
            else:
                r.failures.append(f"rejected alternative gold: {desc}")
        out.append(r)
    return out
