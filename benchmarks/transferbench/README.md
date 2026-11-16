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

## Controlled fixture: `synthetic_controlled_v1.jsonl` (SYNTHETIC)

`benchmarks/transferbench/fixtures/synthetic_controlled_v1.jsonl` holds **48
SYNTHETIC cases**: the 14 smoke cases above, reused verbatim with their IDs, plus
34 new hand-built cases in the same `transferbench-case-v1` schema. It is a
controlled test of whether a transfer policy respects authority, scope,
staleness, consent, security and untrusted text. It is **not** a real research
benchmark, it contains no real repository history, and there are **no
real-agent results** of any kind.

### Honest annotation status

- The file is **single-annotator**. Every label was written by the repository
  author. Each record has `annotator_1: "author"` and `annotator_2: null`. A
  second independent annotator is pending, so there is no inter-rater agreement.
- The first line of the file is a header record (`transferbench-file-header-v1`)
  that repeats this: `synthetic: true`, the single-annotator note and the case
  count. The loader validates it and checks the count.
- The 14 smoke records keep every original key and value. They only gain the
  extra annotation fields described below, so their text is still the placeholder
  `Synthetic preference`. Some smoke records also have a family name that does not
  match their target technology. This is an inherited artifact and was left alone.

### Extra per-case fields

| Field | Meaning |
|---|---|
| `annotator_1`, `annotator_2` | `"author"` and `null` for every case |
| `category` | scope_mismatch, stale_or_superseded, authority_conflict, cross_org_consent_denied, security_constraint, transferable, translatable, ambiguous_evidence or adversarial_text |
| `role` | `trap` (transfer would be wrong) or `positive` (transfer is correct) |
| `pair_id` | links one trap to one matched positive in the same repository family and split |

The loader validates these fields and then drops them, so no policy can read them.

### Design

- 24 matched pairs, each with exactly one trap and one positive, so 24 traps and
  24 positives. A policy that always applies or always abstains scores poorly.
- Labels follow a rubric over structured fields only. Free text inside a decision
  record is data and never changes the label.
  1. Not approved (candidate or revoked) means abstain. Ambiguous-evidence cases
     also accept escalate.
  2. Target owner policy forbids the decision means escalate.
  3. Across projects, a project-scoped decision means abstain. A personal decision
     with a different owner means abstain, because consent is missing.
  4. A blocked tag on the target, or an unmet required tag, means abstain.
  5. Same project, approved and not forbidden means apply.
  6. Across projects, personal, same owner and tags compatible means translate.
- Splits: 16 `train` cases in four families (`training_web`, `training_api`,
  `java_batch`, `rust_cli`) and 32 `test` cases in three families (`python_web`,
  `typescript_api`, `go_payments`). Train and test families are disjoint, and the
  loader rejects leakage. Any single family can also be held out.
- Class coverage: apply 9, translate 15, abstain 18 and escalate 8 cases accept
  each action (the two ambiguous cases accept both abstain and escalate).
  No constant policy can be correct on more than 37.5 percent of cases.
- Near-duplicate guard: token Jaccard over decision text and over label reasons
  must stay at or below 0.6. The current maximum is about 0.41. The 14 smoke
  records are exempt from comparison with each other because they share identical
  placeholder text and differ only in structured fields.

### How to run

```bash
python scripts/evaluate_transferbench.py \
  --cases benchmarks/transferbench/fixtures/synthetic_controlled_v1.jsonl \
  --split test --seed 17 --bootstrap-samples 1000
PYTHONPATH=src python -m pytest -q tests/unit/test_transferbench_controlled_v1.py
```

Use `--split train` for the 16 training cases. The runner compares `no-transfer`,
`full-transfer`, `tags-only` and the conservative `decisionbound` policy with
explicit denominators. A null ratio means its denominator was zero.

### Limits

- Labels were written by the same author as the policy rules, so the
  `decisionbound` agreement with the oracle shows internal consistency with the
  rubric. It does not validate the rubric and is not independent evidence.
- `harmful` counts any apply or translate action the oracle disallows. This
  includes `apply` where the oracle requires `translate`. It is a label
  statistic, not an observed regression.
- 48 constructed cases, three test families and one annotator cannot support
  claims of generalization, population confidence intervals or commercial
  comparisons. Model cost and verified software outcomes stay null.
- Real-repository tasks with two independent annotators are still required before
  any research claim.
