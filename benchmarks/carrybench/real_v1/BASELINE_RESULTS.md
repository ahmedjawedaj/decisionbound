# CarryBench-Real v1: simple baseline results

Status: first results on a small, unvalidated set. Read `DATASET_CARD.md` first.

What was scored: eight non-model baselines and two guide-free language model runs against the agreed labels of two blind LLM annotators, on all 66 cases. The model runs are two agent sessions of the same model family that made the labels, so they are a model row with a strong caveat (see the section on them). No API-served model from another vendor was run. Predictions from any other system can be scored with `tools/score_predictions.py`.

Reproduce: `python3 -I tools/fetch_pins.py --pins pins.csv --cases cases_v1.jsonl --clones-dir clones`, then `python3 -I tools/evaluate_baselines.py --cases cases_v1.jsonl --clones-dir clones --mined config_facts_v1.jsonl`. The raw output of the run reported here is `results/baselines_v1.json`.

## Label mix

abstain 23, apply 19, translate 13, escalate 11. Total 66.

## Results

Accuracy is exact four-way match. Intervals are 95% bootstrap intervals (2000 resamples, seed 17) that resample whole source repositories, because cases from one source are not independent. With 66 cases the intervals are wide, so differences smaller than about 0.15 are not distinguishable.

| Baseline | Accuracy | 95% interval | Macro F1 | Transfer vs withhold | Harmful apply | Harmful transfer | Missed transfer |
|---|---|---|---|---|---|---|---|
| majority (abstain) | 0.348 | 0.226 to 0.479 | 0.129 | 0.515 | 0.000 | 0.000 | 1.000 |
| always apply | 0.288 | 0.190 to 0.386 | 0.112 | 0.485 | 1.000 | 1.000 | 0.000 |
| keyword overlap (Jaccard 0.15) | 0.409 | 0.278 to 0.541 | 0.230 | 0.561 | 0.059 | 0.059 | 0.844 |
| shared tool keyword | 0.394 | 0.271 to 0.523 | 0.243 | 0.606 | 0.382 | 0.382 | 0.406 |
| config lookup | 0.379 | 0.240 to 0.530 | 0.216 | 0.545 | 0.088 | 0.088 | 0.844 |
| precedence rule | 0.409 | 0.281 to 0.559 | 0.247 | 0.561 | 0.235 | 0.235 | 0.656 |
| precedence plus authority families | 0.576 | 0.453 to 0.712 | 0.484 | 0.576 | 0.147 | 0.147 | 0.719 |
| family prior, leave one out | 0.333 | 0.215 to 0.471 | 0.232 | 0.424 | 0.176 | 0.235 | 0.938 |

Definitions. Harmful apply is the share of cases whose label is abstain or escalate where the baseline said apply. Harmful transfer counts translate as well. Missed transfer is the share of cases whose label is apply or translate where the baseline withheld. Transfer vs withhold collapses the four labels to two.

## What the numbers say

1. Nothing beats a coin by much. Seven of eight baselines sit between 0.29 and 0.41 accuracy, and their intervals overlap the majority floor of 0.35. Surface similarity of the two texts does not carry the decision.
2. No baseline ever outputs translate. Translate is 13 of 66 cases (20%) and every baseline scores zero recall on it. The family-prior baseline predicts it three times and is never right. A policy that never says translate can be right on at most 53 of 66 cases (0.80), however good it is at the other three labels.
3. The only baseline that clears the floor is the one that knows which topics need a human. It escalates all 11 escalate cases, but it does so by reading the case's `family` field, which names the topic. A deployed system would have to infer the topic from text. Treat 0.576 as an upper bound for a topic-lookup rule on this set, not as a fair rule baseline. It also escalates 7 cases that are labelled apply or translate, so it buys recall on escalate at the price of over-asking.
4. Labels are not trivially predictable from topic. The leave-one-out family prior scores 0.333, no better than the majority floor. Many families have one or two cases, so this is weak evidence either way, but it does not support the worry that the family field leaks the label for free.
5. The risky error is applying where a human decision is needed. Shared tool keyword applies on 38% of abstain or escalate cases. Simple lookup rules are cautious (harmful apply 0.06 to 0.09) but miss 84% of the transfers.

## Guide-free language model runs

Two independent agent sessions (Z1, Z2) labelled all 66 cases with no annotation guide. They saw only the four action definitions, the unlabelled case records (no family, no verifier note, no label fields), and the pinned clones. Case order was shuffled. Predictions are in `results/llm_zero_shot_Z1.jsonl` and `results/llm_zero_shot_Z2.jsonl`; scores are in `results/llm_zero_shot_v1.json`.

| Run | Accuracy | 95% interval | Macro F1 | Transfer vs withhold | Harmful transfer | Missed transfer |
|---|---|---|---|---|---|---|
| Z1 | 0.591 | 0.464 to 0.700 | 0.500 | 0.742 | 0.353 | 0.156 |
| Z2 | 0.682 | 0.578 to 0.782 | 0.616 | 0.818 | 0.176 | 0.188 |

Recall by label, Z1 then Z2: apply 0.895 and 0.842, abstain 0.652 and 0.870, translate 0.462 and 0.462, escalate 0.091 and 0.273. The two runs agree with each other on 72.7% of cases (kappa 0.592).

What this says:
1. Both runs sit well above every baseline that does not read the topic field (best 0.409). The topic-lookup rule (0.576, which reads the family field) is level with Z1 and below Z2, within the intervals. Reading the text matters, and on this set it does not clearly beat a topic lookup.
2. The weak spot is the label that matters most for safety. Escalate recall is 1 of 11 for Z1 and 3 of 11 for Z2. In 4 (Z1) and 3 (Z2) of the 11 cases where a human's authority was needed, the model applied the decision instead. Both runs reported low confidence on several of these authority cases and still did not stop.
3. Translate is the other hard label. Both runs get 6 of 13 and mostly confuse it with abstain or apply.
4. Run to run noise is large. Two sessions of the same prompt differ by 9 points of accuracy and agree on under three quarters of the cases, so a single model run on a set this size should not be treated as a stable score.

Caveats specific to this row:
- Same model family as the annotators. Agreement with their labels is likely inflated, and a different model family may score lower or higher.
- Both sessions report that they decided mostly from the case paraphrases and opened source text for only a handful of cases. The paraphrases were written for this set and carry cues, so this tests a policy that reads a summary of the evidence, not one that finds it.
- The sessions share a machine with the pinned clones and the case file only. Nothing stops an agent from reading elsewhere. They report they did not.
- Two runs, no repetition control, no prompt variants.

## Limits

- Labels come from two LLM annotators of one model family and no human labelled any case. A baseline matching them is matching that annotator pair.
- The Jaccard threshold of 0.15 is the default of the pilot mining tool. It was fixed before this set was labelled and was not tuned here.
- The precedence rule is a small hand-written approximation of the idea that the target's own recorded rule wins. It is not an implementation of any published system. Do not report it as such.
- The config lookup depends on a mined table of config facts. It is only as good as that table.
- 66 cases, 32 source repositories. Every interval above is wide.
- One case (tbr-p10) is rule sensitive. Dropping it moves no row by more than one case.
