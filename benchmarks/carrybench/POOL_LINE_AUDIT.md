# Line audit of the mined config-fact pools

The mined pools (`real_v1/config_facts_v1.jsonl` and `real_v2_holdout/config_facts_v2_holdout.jsonl`) hold 584 pointers that name a line. None of them carries a text hash. The miner found each line by searching the file for the first line that contains a needle such as `"type"` or `line-length`, so a pointer can name a different line from the one it claims. Two such lines were found by reading in the v2 holdout (see `real_v2_holdout/errata_v2_holdout_v1.jsonl`). This page reports an audit of every line in both pools.

## Method

`real_v1/tools/audit_pool_lines.py` reads each pointed-to file at its pinned commit and checks the recorded line by structure and value, using the miner's own fact tables:

- `package.json`: the top-level `type` or `packageManager` key with the recorded value, or an entry of `devDependencies` or `dependencies` for the recorded tool.
- `pyproject.toml`: a real table header for the tool, `requires-python` under `[project]`, `line-length` under the right tool's table with the recorded number, or the pydocstyle `convention` with the recorded value.

Comments and the inside of multi-line strings do not count as TOML. The tool also suggests the line that fits. It checks structure and value, not meaning. A pointer that passes holds the claimed key in the right place, and that does not make it a good case.

The committed result is `pool_line_audit_v1.json`. It was computed from files fetched at the pinned commits (126 files, none missing, none unauditable). The test `test_committed_report_is_what_the_tool_computes_from_the_pinned_files` recomputes it when `CARRYBENCH_FILES` points at such a directory and is skipped otherwise.

## Result

48 of 584 pointers (8%) name the wrong line, in 36 repositories. 20 are in the v1 pool and 28 in the v2 holdout pool.

| Method | Checked | Wrong |
|---|---|---|
| `ruff_pydocstyle_convention` | 15 | 8 (53%) |
| `package_type_field` | 37 | 12 (32%) |
| `black_line_length` | 5 | 1 (20%) |
| `dependency_declared` | 157 | 22 (14%) |
| `pyproject_tool_table` | 237 | 4 (2%) |
| `ruff_line_length` | 40 | 1 (2%) |
| `packageManager_field` | 39 | 0 |
| `requires_python` | 54 | 0 |

The wrong lines fall into four shapes:

- A `"type"` that belongs to `repository` (`"git"`), not the module type (12).
- A script that runs a tool, or a top-level config key for it, in place of the dependency declaration (22).
- A comment or an unrelated key that mentions `convention`, or a comment that names a table, in place of the real line (12).
- A `line-length` under the other tool's table with the same number (2).

Every wrong pointer has a suggested line in the report, and the fact itself holds in the file in all 48.

## What this does and does not affect

- The audit agrees with the by-hand reading behind the erratum on all ten cases: the two wrong pointers (cbh-006 and cbh-039) are flagged and the eight confirmed ones are not.
- Case files (Real v1, the v2 holdout candidates and the excluded cases) use 24 distinct config-fact line pointers. Only those two are flagged. No label, score or frozen case file changes.
- The other 46 wrong pointers sit in pool rows that no case uses. The pools are frozen and unchanged. Do not build a case from a pool row without checking its line, and use this report to find the rows to avoid or correct.
- The miner still finds lines by first match, so a new pool would repeat the problem. Fixing it is a separate change.
