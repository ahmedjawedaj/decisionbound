# DecisionBound's own transfer policy on CarryBench-Real v1

Status: first measurement of the shipped policy on real cases. Read `DATASET_CARD.md` first. Labels come from two blind LLM annotators, so this measures agreement with that pair and not correctness.

What was scored: the four methods defined in `src/decisionbound/transferbench.py` (`no-transfer`, `full-transfer`, `tags-only`, `decisionbound`), unchanged, on all 66 cases. The shipped methods read a `Decision` and a `Project`. They do not read repository text. A label-blind adapter (`tools/evaluate_decisionbound.py`) builds those objects from each case, using only the source text, the target's mined config facts and the pinned language. The adapter is not part of DecisionBound and its choices move the result, so three settings are shown.

Reproduce: `PYTHONPATH=src python3 -I tools/evaluate_decisionbound.py --cases cases_v1.jsonl --clones-dir clones --mined config_facts_v1.jsonl --pins pins.csv`. Raw output is `results/decisionbound_v1.json`.

## Settings

| Setting | Meaning |
|---|---|
| faithful | Each source decision is project scoped, which is what a decision recorded in one repository is. The shipped policy never carries those across. |
| personal | Each decision is declared personal with the same owner on both sides. This is the most generous reading the policy allows. |
| personal plus blocklist | As personal, and the owner has put every authority topic (licence, security reporting, public actions, AI disclosure, release publishing) in `forbidden_ids`. This reads the case's topic field, so it shows what an owner maintained blocklist would buy and is not something the policy does alone. |

## Results

Accuracy is exact four-way match against the agreed labels. Intervals are 95% bootstrap intervals over source repositories (2000 resamples, seed 17). Harmful transfer is the share of abstain or escalate cases where the method said apply or translate.

| Method and setting | Accuracy | 95% interval | Macro F1 | Harmful transfer | Missed transfer |
|---|---|---|---|---|---|
| no-transfer | 0.348 | 0.226 to 0.479 | 0.129 | 0.000 | 1.000 |
| full-transfer | 0.288 | 0.190 to 0.386 | 0.112 | 1.000 | 0.000 |
| tags-only | 0.409 | 0.274 to 0.548 | 0.254 | 0.735 | 0.062 |
| decisionbound, faithful | 0.348 | 0.226 to 0.479 | 0.129 | 0.000 | 1.000 |
| decisionbound, personal | 0.318 | 0.200 to 0.442 | 0.221 | 0.735 | 0.062 |
| decisionbound, personal plus blocklist | 0.409 | 0.306 to 0.520 | 0.392 | 0.412 | 0.281 |

The first three rows are identical in all settings because the shipped baselines ignore scope and the blocklist.

## What the numbers say

1. In the faithful setting the shipped policy is the same as always abstain. It cannot do otherwise: it refuses every project scoped decision from another repository. That is safe and it is the intended default, and it also means it never produces a transfer on real repositories unless an owner re-declares decisions as personal.
2. In the most generous setting it says translate for 55 of 66 cases and abstain for 11. It gets 12 of 13 translate cases and 9 of 23 abstain cases right, and it never says apply or escalate on its own. Harmful transfer is 0.735, so a policy that trusts its technology tags alone would carry a decision over in 25 of the 34 cases where a careful engineer would not.
3. The tag test is weak because the cheap tags it can compute are weak. It required a tool the source prescribes to be present in the target. That removes only the cases where a named tool is absent, and most abstain cases are ones where the target uses a different tool or has its own rule on the topic.
4. An owner blocklist is worth something. It lifts escalate recall from 0 to 11 of 11 and cuts harmful transfer from 0.735 to 0.412. The price is 5 translate cases and 2 apply cases that get escalated instead.
5. No setting beats 0.41. The intervals overlap those of the plain baselines in `BASELINE_RESULTS.md`, so this set cannot rank the shipped policy above a keyword rule.
6. The policy has no way to output apply across repositories. 19 of 66 cases (29%) are apply, so its apply recall is zero by construction. Whether apply across repositories should ever be automatic is a design question for the owner, and the labels suggest the cases exist.

## What this does and does not show

It shows that the shipped policy is a conservative authority gate and not a transfer predictor. It tells you the missing input: the target repository's own recorded stance on the topic, which the policy cannot read today. It is evidence for a design change, not for a claim that the policy is wrong.

It does not show how a policy that does read target stance would score. A rule set written from the annotation guide would score high on these labels by construction, because the labels were made with that guide. That would be circular and is deliberately not reported. A fair test of any new policy needs new cases labelled after the policy is fixed, ideally by a human.

Other limits: labels are from LLM annotators of one family; 66 cases and wide intervals; the adapter's tag choices (required tags from prescribed tools, no blocked tags) are one reasonable reading and not the only one.
