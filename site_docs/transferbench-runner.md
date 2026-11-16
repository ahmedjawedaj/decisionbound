# TransferBench: deterministic policy evaluation runner

DecisionBound now includes a **policy-oracle evaluation instrument**. This is
not yet a scientific result or a reproduction of agent success: it evaluates
only whether four fixed decision-transfer policies match a manually supplied
set of acceptable actions. It does **not** run LLMs, write patches, verify
software tasks, or estimate actual token costs.

## Baselines

| Arm | Mechanism | Important limitation |
|---|---|---|
| `no-transfer` | Always abstain | Misses safe opportunities |
| `full-transfer` | Apply every approved decision | Ignores repository owners and conflicts |
| `tags-only` | Apply when required tags are present | Ignores blocked tags, owner, scope and target policies |
| `decisionbound` | Existing conservative policy | Hand-crafted rule, not a learned or empirically proven method |

## Deterministic smoke run

```bash
python scripts/evaluate_transferbench.py \
  --cases benchmarks/transferbench/fixtures/synthetic_smoke_v1.jsonl \
  --split test --seed 17 --bootstrap-samples 1000 \
  --output /tmp/transferbench-smoke.json
```

The 14 handcrafted toy records comprise two `train` cases and 12 `test` cases
across three **synthetic** held-out repository families. These labels are useful
for testing parsers, action policies, safe denominators, and family-cluster
resampling, but the fixture authors are also the code authors. The results
cannot support a publication, commercial benchmark comparisons, or claims of
negative-transfer reduction on actual repositories.

## What the report means

- `oracle_action_acceptance`: fraction of records whose action is included in
  the independently recorded set of acceptable actions.
- `harmful_per_transfer`: **oracle-disallowed transfer actions** divided by
  all apply/translate actions; null when no transfers occur. This is not the
  observed rate of real regressions.
- `harmful_per_conflict_opportunity`: disallowed apply/translate actions per
  labeled conflicting case; null when no conflicting cases exist.
- `abstention_precision` and `abstention_recall`: decision-label statistics,
  not downstream correctness.
- `oracle_action_acceptance_95ci`: deterministic bootstrap of repository
  families where at least two are present; null otherwise. Do not interpret
  confidence intervals on 14 constructed cases as population uncertainty.
- `verified_software_tasks` and `model_cost_usd`: deliberately null.

The runner validates unique IDs, a strict versioned JSONL schema, explicit
annotation reasons, disjoint train/test target-repository families, and bounded
inputs. It emits only aggregate results and data/policy hashes (not source text).
Output files cannot overwrite existing paths.

## Research next steps, not completed

1. Collect licensed pinned public-repository tasks with author/maintainer
   permissions where needed, an independent oracle, and held-out families.
2. Have multiple annotators independently label conflict scenarios; publish
   adjudication and agreement rates before running the treatment method.
3. Pre-register baseline selection, task stratification, budgets, and exclusion
   rules before accessing held-out results.
4. Execute identical model/harness and verifier configurations, collecting
   actual quality, token and cost outcomes. Pair trials by task and cluster by
   repository; measure cost per *verified* successful software task.
5. Report counterexamples, raw paired outcomes, uncertainty, and negative
   results instead of converting fixture scores into product claims.

See [Research](research.md), [Verification](verification.md) and
[Production readiness](production-readiness.md).
