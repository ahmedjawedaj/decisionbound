"""Pointers that name lines but carry no text hash (CarryBench issue 21).

Offline except for one test that needs the pinned clones. The frozen v2 holdout
files are never edited, so the erratum has to account for every unhashed line
pointer in them, and no other case file may add one.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "carrybench"
V2 = BENCH / "real_v2_holdout"
CASES = V2 / "cases_v2_holdout_v1.jsonl"
FROZEN = [V2 / "cases_v2_holdout_candidates.jsonl", CASES, V2 / "excluded_v2_holdout.jsonl"]
ERRATA = V2 / "errata_v2_holdout_v1.jsonl"
MINER = BENCH / "real_v1" / "tools" / "mine_decisions.py"
HEX64 = "0123456789abcdef"


# Hashes of the pinned source lines, recomputed from the pinned sources by the author and by an independent reviewer.
# Pinning them here means a corrupted erratum fails offline, where the pinned clones are not available.
PINNED = {
    "cbh-001": ("9fa5cfc2fd8374f3b485f96af67ef23d6dbcfefbdb63cdda3ca5fa9f9233bf48", "9fa5cfc2fd8374f3b485f96af67ef23d6dbcfefbdb63cdda3ca5fa9f9233bf48"),
    "cbh-002": ("e99e9e0f22b077eeb82b7883fe0e6f19d6fbfed0be95330dff69404a375b9db0", "e99e9e0f22b077eeb82b7883fe0e6f19d6fbfed0be95330dff69404a375b9db0"),
    "cbh-006": ("1c21e7109507d03db11350899bdad97e05e89f7020ca2200e9a8b32bddb7d936", "059d1c7bde504bc87cf4b2441ea58282b65f5f6707a1db7268517fa3fa4891a3"),
    "cbh-010": ("b76ab4e3ac97898b89f31940f1e00b663ec0c7e95f8ab4978b728b3dcddfb371", "b76ab4e3ac97898b89f31940f1e00b663ec0c7e95f8ab4978b728b3dcddfb371"),
    "cbh-013": ("57ab93f91c352fc3c558bc5039512c322c78063e917d62b44c825082a5a1e5fb", "57ab93f91c352fc3c558bc5039512c322c78063e917d62b44c825082a5a1e5fb"),
    "cbh-017": ("dc652e4e54c7bf0190f1b4e180bd2b8b808562716bea534fa9ce2aaec8418f93", "dc652e4e54c7bf0190f1b4e180bd2b8b808562716bea534fa9ce2aaec8418f93"),
    "cbh-022": ("253d190948d085b720999fa0901cc8d5ce341ed729eab98fef4a2ebd083476ad", "253d190948d085b720999fa0901cc8d5ce341ed729eab98fef4a2ebd083476ad"),
    "cbh-029": ("1dd26c846eb63fc62c77c75b94f1d9bc17d572f4e2e5fa019cf921631703de06", "1dd26c846eb63fc62c77c75b94f1d9bc17d572f4e2e5fa019cf921631703de06"),
    "cbh-036": ("eaea8faee78a8f384d8201a4ece2a9d8fffc85d4c361ef4ff77c7828d371e03b", "eaea8faee78a8f384d8201a4ece2a9d8fffc85d4c361ef4ff77c7828d371e03b"),
    "cbh-039": ("3e146e2af09bd19163f856a82d7e15bd3ab14b63b46702cb175d7decfef5e6b5", "3871b85b5df6800a819e98a52d8116da640453342fb8d4ca35fbf32a42cfece1"),
}


def load_tool(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


errata_tool = load_tool("errata_tool", V2 / "tools" / "errata.py")
miner = sys.modules["mine_decisions"]


def read(path):
    return errata_tool.read_cases(path)


# ---------------------------------------------------------------- the real data

def frozen_cases():
    return [c for f in FROZEN for c in read(f)]


def test_erratum_accounts_for_exactly_the_unhashed_line_pointers_in_the_frozen_files():
    errata = read(ERRATA)
    assert errata_tool.check(errata, frozen_cases(), None) == []
    assert len(errata) == 10  # nine scored cases plus cbh-017 from the excluded set


def test_erratum_header_names_the_files_it_covers():
    header = json.loads(ERRATA.read_text(encoding="utf-8").splitlines()[0])
    assert header["applies_to"] == [f.name for f in FROZEN]
    assert header["record_count"] == len(read(ERRATA))
    assert all(f.exists() for f in FROZEN)


def test_erratum_flags_only_the_two_pointers_that_are_off():
    wrong = {(r["case_id"], r["recorded"]["line_start"], r["corrected"]["line_start"], r["corrected"]["line_end"])
             for r in read(ERRATA) if r["status"] == "wrong_location"}
    assert wrong == {("cbh-006", 330, 342, 343), ("cbh-039", 29, 69, 69)}


def test_erratum_hashes_equal_the_pinned_values():
    got = {r["case_id"]: (r["corrected"]["text_sha256"], r["recorded"]["text_sha256"]) for r in read(ERRATA)}
    assert got == PINNED


def test_erratum_records_hold_hashes_and_no_third_party_text():
    allowed = {"case_id", "repo", "sha", "path", "kind", "recorded", "status", "corrected", "note"}
    for r in read(ERRATA):
        assert set(r) == allowed
        assert set(r["recorded"]) == set(r["corrected"]) == {"line_start", "line_end", "text_sha256"}
        for span in (r["recorded"], r["corrected"]):
            assert len(span["text_sha256"]) == 64 and set(span["text_sha256"]) <= set(HEX64)
        assert len(r["sha"]) == 40


def test_the_frozen_case_files_still_have_the_null_hashes():
    # The erratum corrects nothing in place. If these hashes were filled in, a frozen file was edited.
    pointers = [p for c in read(CASES) for p in errata_tool.unhashed_line_pointers(c)]
    assert len(pointers) == 9
    assert {p["line_start"] for p in pointers if p["path"] == "pyproject.toml"} >= {330}


def test_no_other_case_file_has_a_line_pointer_without_a_hash():
    allowed = {(r["case_id"], r["path"], r["recorded"]["line_start"]) for r in read(ERRATA)}
    seen = 0
    for path in sorted(BENCH.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not str(row.get("format", "")).startswith(("carrybench-real-case", "transferbench-real-case")):
                continue
            for p in errata_tool.unhashed_line_pointers(row):
                seen += 1
                assert path in FROZEN and (row["case_id"], p["path"], p["line_start"]) in allowed, \
                    f"{path.name} {row['case_id']} {p['path']}:{p['line_start']} has lines and no text hash"
    assert seen >= 10


# ----------------------------------------------------- check() fails when it should

def _data():
    return frozen_cases(), read(ERRATA)


def test_check_reports_an_unlisted_pointer():
    cases, errata = _data()
    problems = errata_tool.check(errata[1:], cases, None)
    assert any(p.startswith("UNLISTED") for p in problems)


def test_check_reports_a_stale_record():
    cases, errata = _data()
    extra = copy.deepcopy(errata[0])
    extra["recorded"]["line_start"] = extra["recorded"]["line_end"] = 999
    extra["corrected"]["line_start"] = extra["corrected"]["line_end"] = 999
    assert any(p.startswith("STALE") for p in errata_tool.check(errata + [extra], cases, None))


def test_check_reports_a_duplicate_record():
    cases, errata = _data()
    assert any(p.startswith("DUPLICATE") for p in errata_tool.check(errata + [errata[0]], cases, None))


def test_check_reports_a_status_that_disagrees_with_the_lines():
    cases, errata = _data()
    bad = copy.deepcopy(errata)
    next(r for r in bad if r["status"] == "wrong_location")["status"] = "location_confirmed"
    assert any(p.startswith("STATUS") for p in errata_tool.check(bad, cases, None))
    bad = copy.deepcopy(errata)
    next(r for r in bad if r["status"] == "location_confirmed")["status"] = "wrong_location"
    assert any(p.startswith("STATUS") for p in errata_tool.check(bad, cases, None))


def test_check_reports_equal_hashes_for_a_moved_span_and_unequal_for_an_unmoved_one():
    cases, errata = _data()
    bad = copy.deepcopy(errata)
    r = next(r for r in bad if r["status"] == "wrong_location")
    r["corrected"]["text_sha256"] = r["recorded"]["text_sha256"]
    assert any(p.startswith("HASH") for p in errata_tool.check(bad, cases, None))
    bad = copy.deepcopy(errata)
    r = next(r for r in bad if r["status"] == "location_confirmed")
    r["corrected"]["text_sha256"] = "0" * 64
    assert any(p.startswith("HASH") for p in errata_tool.check(bad, cases, None))


# ------------------------------------------- hashing and the verifier on a real git repo

CONFIG = "a = 1\n[tool.x]\nname = 'old'\nconvention = 'google'\nz = 2\n"


@pytest.fixture()
def repo(tmp_path):
    clones = tmp_path / "clones"
    work = clones / "own__proj"
    work.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    run = lambda *a: subprocess.run(["git", "-C", str(work), *a], check=True, capture_output=True, text=True, env=env)
    run("init", "-q")
    (work / "pyproject.toml").write_text(CONFIG, encoding="utf-8")
    run("add", "pyproject.toml")
    run("commit", "-q", "-m", "x", "--no-gpg-sign")
    return clones, run("rev-parse", "HEAD").stdout.strip()


def _case(sha, start, end, digest):
    ev = {"kind": "config_fact:pyproject_tool_table", "path": "pyproject.toml", "line_start": start, "line_end": end,
          "text_sha256": digest, "paraphrase": "the convention is google"}
    return {"case_id": "cbh-900", "format": "carrybench-real-case-v1",
            "source": {"repo": "own/proj", "sha": sha, "path": "", "line_start": 0, "line_end": 0},
            "target": {"repo": "own/proj", "sha": sha, "evidence": [ev]}}


def _verify(tmp_path, case, clones, *flags):
    f = tmp_path / "cases.jsonl"
    f.write_text(json.dumps(case) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", str(MINER), "verify", "--cases", str(f), "--clones-dir", str(clones), *flags],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout), proc.stderr


def test_verifier_default_still_accepts_a_null_hash_and_counts_it(tmp_path, repo):
    clones, sha = repo
    rc, out, _ = _verify(tmp_path, _case(sha, 1, 1, None), clones)  # line 1 is not the convention, and it passes
    assert rc == 0 and out["mismatches"] == 0 and out["unhashed_line_pointers"] == 1 and out["unhashed_rejected"] == 0


def test_verifier_require_hash_fails_on_a_null_hash_with_lines(tmp_path, repo):
    clones, sha = repo
    for start in (1, 4):  # a wrong line and a right line both fail without a hash
        rc, out, err = _verify(tmp_path, _case(sha, start, start, None), clones, "--require-hash")
        assert rc == 1 and out["unhashed_rejected"] == 1 and "UNHASHED cbh-900" in err
        assert out["mismatches"] == 0  # a rejected null hash is not a hash mismatch


def test_verifier_require_hash_passes_a_correct_hash_and_fails_a_wrong_one(tmp_path, repo):
    clones, sha = repo
    good = miner.line_span_hash(CONFIG, 4, 4)
    assert _verify(tmp_path, _case(sha, 4, 4, good), clones, "--require-hash")[0] == 0
    rc, out, err = _verify(tmp_path, _case(sha, 3, 3, good), clones, "--require-hash")
    assert rc == 1 and "MISMATCH cbh-900" in err and "UNHASHED" not in err


def test_verifier_require_hash_ignores_presence_only_pointers(tmp_path, repo):
    clones, sha = repo
    rc, out, _ = _verify(tmp_path, _case(sha, 0, 0, None), clones, "--require-hash")
    assert rc == 0 and out["unhashed_line_pointers"] == 0


def test_require_hash_does_not_report_unhashed_when_the_file_is_missing(tmp_path, repo):
    clones, sha = repo
    case = _case(sha, 4, 4, None)
    case["target"]["evidence"][0]["path"] = "nope.toml"
    rc, out, err = _verify(tmp_path, case, clones, "--require-hash")
    assert rc == 1 and "MISMATCH" in err and "UNHASHED" not in err


def test_build_hashes_both_spans_and_check_verifies_them_against_the_clone(tmp_path, repo, monkeypatch):
    clones, sha = repo
    case = _case(sha, 3, 3, None)  # recorded line holds name = 'old', the convention is on line 4
    monkeypatch.setitem(errata_tool.CORRECTIONS, ("cbh-900", "pyproject.toml"), (4, 4))
    recs = errata_tool.build([case], clones)
    assert len(recs) == 1 and recs[0]["status"] == "wrong_location"
    assert recs[0]["recorded"]["text_sha256"] == miner.line_span_hash(CONFIG, 3, 3)
    assert recs[0]["corrected"]["text_sha256"] == miner.line_span_hash(CONFIG, 4, 4)
    assert errata_tool.check(recs, [case], clones) == []
    tampered = copy.deepcopy(recs)
    tampered[0]["corrected"]["text_sha256"] = "1" * 64
    assert any(p.startswith("MISMATCH") for p in errata_tool.check(tampered, [case], clones))
    # a pointer that is not in CORRECTIONS is recorded as confirmed, with equal spans and equal hashes
    monkeypatch.delitem(errata_tool.CORRECTIONS, ("cbh-900", "pyproject.toml"))
    same = errata_tool.build([case], clones)[0]
    assert same["status"] == "location_confirmed" and same["recorded"] == same["corrected"]


def test_build_refuses_a_span_outside_the_file(tmp_path, repo):
    clones, sha = repo
    with pytest.raises(SystemExit):
        errata_tool.build([_case(sha, 99, 99, None)], clones)


def test_require_hash_also_rejects_an_unhashed_source_pointer(tmp_path, repo):
    clones, sha = repo
    case = _case(sha, 4, 4, miner.line_span_hash(CONFIG, 4, 4))
    case["source"].update(path="pyproject.toml", line_start=4, line_end=4, text_sha256=None)
    assert _verify(tmp_path, case, clones)[0] == 0
    rc, out, err = _verify(tmp_path, case, clones, "--require-hash")
    assert rc == 1 and out["unhashed_rejected"] == 1 and "UNHASHED cbh-900 source" in err


def test_unhashed_line_pointers_includes_the_source_pointer():
    case = _case("a" * 40, 4, 4, "f" * 64)
    case["source"].update(path="AGENTS.md", line_start=8, line_end=8, text_sha256=None)
    assert [p["path"] for p in errata_tool.unhashed_line_pointers(case)] == ["AGENTS.md"]


def test_check_reports_a_record_whose_repo_sha_or_kind_differs_from_its_case():
    cases, errata = _data()
    for field, value in (("repo", "other/repo"), ("sha", "b" * 40), ("kind", "config_fact:other")):
        bad = copy.deepcopy(errata)
        bad[0][field] = value
        assert any(p.startswith("ORIGIN") for p in errata_tool.check(bad, cases, None)), field


def test_build_lists_a_pointer_once_when_the_case_is_in_several_files(tmp_path, repo):
    clones, sha = repo
    case = _case(sha, 4, 4, None)
    assert len(errata_tool.build([case, copy.deepcopy(case), copy.deepcopy(case)], clones)) == 1


def test_span_hash_refuses_line_zero_a_reversed_span_and_a_span_past_the_end(tmp_path, repo):
    clones, sha = repo
    span = lambda start, end: errata_tool._span_hash(clones, "own/proj", sha, "pyproject.toml", start, end)
    assert span(1, 1) == miner.line_span_hash(CONFIG, 1, 1)
    assert span(0, 1) is None and span(4, 3) is None and span(1, 6) is None


# ----------------------------------------------------------------- pinned clones

@pytest.mark.skipif(not os.environ.get("CARRYBENCH_CLONES"), reason="pinned clones not available")
def test_erratum_hashes_match_the_pinned_clones():
    clones = Path(os.environ["CARRYBENCH_CLONES"])
    problems = errata_tool.check(read(ERRATA), frozen_cases(), clones)
    assert problems == []
