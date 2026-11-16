# TransferBench

The baseline directory contains a **14-case synthetic policy-oracle smoke
fixture**, not a real research benchmark. Each JSONL record provides a versioned
case ID, split, target repository family, source decision, target project,
explicit forbidden IDs, independent allowed-action labels, conflict marker,
and annotation rationale. The current fixture was hand-authored in this
repository and cannot establish empirical generalization.

Run `python scripts/evaluate_transferbench.py --cases benchmarks/transferbench/fixtures/synthetic_smoke_v1.jsonl --split test`.
The runner tests `no-transfer`, `full-transfer`, `tags-only`, and the conservative
DecisionBound policy and emits bounded, labeled statistics with deterministic
cluster bootstrap. All real model cost, code correctness, and successful task
metrics remain null. See `site_docs/transferbench-runner.md` and
the evaluation methodology before making comparative claims.
