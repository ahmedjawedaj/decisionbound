# CarryBench

When a human has approved a decision in repository A, what should an agent do with it in repository B? CarryBench measures that: carry it over as is, adapt it, leave it, or stop and ask a person.

| Tier | Where | What it is |
|---|---|---|
| Synthetic | `benchmarks/transferbench/` | Handwritten fixtures and a deterministic oracle runner. Tests a policy's logic, says nothing about real repositories. |
| Real v1 | `benchmarks/carrybench/real_v1/` | 66 labelled cases from 50 permissively licensed public repositories, pinned to commits, with pointers and hashes only and no third party text. Labels come from two blind LLM annotators. No human has labelled any case. |
| Real v2 holdout | `benchmarks/carrybench/real_v2_holdout/` | 35 labelled cases (40 candidates, 5 contested and excluded) from 77 repositories not in Real v1. The candidates were committed unlabelled, the labels were frozen from two blind LLM annotators, and only then were systems scored. No human has labelled any case. |

## What the first results say

On Real v1, scored against the agreed labels (details and limits in `real_v1/BASELINE_RESULTS.md` and `real_v1/DECISIONBOUND_RESULTS.md`):

- Keyword and lookup rules score 0.29 to 0.41 accuracy. Always abstaining scores 0.35.
- DecisionBound's shipped transfer policy, which refuses project scoped decisions from other repositories, behaves like always abstain (0.35). Declared as personal, it says translate on 55 of 66 cases and carries a decision over in 25 of the 34 cases where a careful engineer would not.
- Two guide-free language model runs score 0.59 and 0.68. They rarely stop for a human: they escalated 1 and 3 of the 11 cases where a person's authority was needed.

On the Real v2 holdout (35 cases, labels frozen before scoring, see `real_v2_holdout/HOLDOUT_RESULTS.md`):

- The shipped policy again behaves like always abstain in its default setting (0.37). Its best setting scores 0.51, with an interval that overlaps always abstaining, so the earlier statement that no setting beats 0.41 does not hold on this set.
- The best simple rule (authority topics escalate, otherwise compare the target's strongest evidence) scores 0.77 and the shipped policy never returns apply for another repository. Part of that rule's lead comes from mirroring a rule in the annotation guide.
- A draft annotation guide v1.2 (`real_v1/guides/guide_v1_2.md`) proposes rules for five ambiguities that the two blind annotators reported on the v2 holdout, plus one pitfall noted by annotator A. It has labelled no case, it applies to new cases only, and because it was written after reading the holdout disagreements it cannot be scored on that holdout.
- The two sets differ in difficulty, so their scores are not directly comparable.

These are small unvalidated sets with wide intervals. They are not a ranking of any product.

## Try it offline in one command

No account, key or network is needed to score the stored predictions:

```bash
cd benchmarks/carrybench/real_v1
python3 -I tools/score_predictions.py --cases cases_v1.jsonl \
  --pred Z1=results/llm_zero_shot_Z1.jsonl --pred Z2=results/llm_zero_shot_Z2.jsonl
python3 -I verifiers/run_verifiers.py
```

To rebuild the evidence from source, `tools/fetch_pins.py` clones the pinned repositories and `tools/mine_decisions.py verify` re-hashes every pointer.

## Score your own system

Write one JSONL line per case, `{"case_id": ..., "label": "apply|translate|abstain|escalate"}`, and run `score_predictions.py --pred NAME=yourfile.jsonl`. It reports accuracy, macro F1, harmful transfer and missed transfer with confidence intervals that resample source repositories. Say which dataset version you scored and whether you tuned on it. A pull request leaderboard is planned only after a human labelled holdout exists, so that a high score cannot just mean agreement with one model family.

## Limits you should know first

See `real_v1/DATASET_CARD.md`: LLM only annotators, contested cases excluded, 66 cases, static pinned snapshots.
