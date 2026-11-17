# Holdout results, CarryBench-Real v2

These are the first results on a set whose labels were frozen before any system was scored on it. The candidate commit is fe6619f (02:42 local time on 2026-11-17), the label freeze is 5a058e5 (02:55), and the results were produced after that. No rule, threshold or policy was changed between the first release and this run. The scorers are the unchanged tools in `../real_v1/tools/`.

Labels come from two blind AI annotators of one model family and no human has labelled any case (see `AGREEMENT_REPORT.md`). Scores measure agreement with that annotator pair. They are not accuracy against human judgement.

## Set

35 scored cases (abstain 13, apply 12, escalate 6, translate 4) from 40 candidates. Five contested cases are excluded. Intervals are 95 percent bootstrap intervals that resample source repositories (2000 resamples, seed 17). With 35 cases they are wide.

## Results

| System | Accuracy | 95% interval | Macro F1 | Harmful transfer | Missed transfer |
|---|---|---|---|---|---|
| Always abstain | 0.371 | 0.206 to 0.528 | 0.135 | 0.000 | 1.000 |
| DecisionBound shipped policy, faithful setting | 0.371 | 0.206 to 0.528 | 0.135 | 0.000 | 1.000 |
| DecisionBound, decisions declared personal | 0.457 | 0.294 to 0.618 | 0.302 | 0.368 | 0.125 |
| DecisionBound, personal plus owner blocklist | 0.514 | 0.343 to 0.686 | 0.380 | 0.053 | 0.562 |
| Tags only (no authority logic) | 0.629 | 0.471 to 0.778 | 0.374 | 0.368 | 0.125 |
| Target config uses the same tool | 0.600 | 0.441 to 0.765 | 0.362 | 0.053 | 0.438 |
| Tool and tier precedence | 0.600 | 0.441 to 0.765 | 0.358 | 0.053 | 0.375 |
| Authority topics escalate, else precedence | 0.771 | 0.618 to 0.912 | 0.602 | 0.053 | 0.438 |
| Keyword overlap | 0.400 | 0.235 to 0.559 | 0.177 | 0.000 | 0.938 |
| Config lookup | 0.486 | 0.324 to 0.647 | 0.273 | 0.000 | 0.750 |
| Topic prior, leave one out | 0.571 | 0.412 to 0.722 | 0.445 | 0.211 | 0.562 |

Recall by label for the main systems (correct of total):

| System | abstain | apply | escalate | translate |
|---|---|---|---|---|
| DecisionBound faithful | 13 of 13 | 0 of 12 | 0 of 6 | 0 of 4 |
| DecisionBound personal | 12 of 13 | 0 of 12 | 0 of 6 | 4 of 4 |
| DecisionBound personal plus blocklist | 12 of 13 | 0 of 12 | 6 of 6 | 0 of 4 |
| Target config uses the same tool | 12 of 13 | 9 of 12 | 0 of 6 | 0 of 4 |
| Authority topics escalate, else precedence | 12 of 13 | 9 of 12 | 6 of 6 | 0 of 4 |

## Against the first set

| System | First set (66 cases) | Holdout (35 cases) |
|---|---|---|
| Always abstain | 0.348 | 0.371 |
| DecisionBound faithful | 0.348 | 0.371 |
| DecisionBound personal plus blocklist | 0.409 | 0.514 |
| Authority topics escalate, else precedence | 0.576 | 0.771 |
| Target config uses the same tool | 0.394 | 0.600 |

What held up:
- The shipped policy in its default setting behaves exactly like always abstaining on both sets.
- On both sets the best simple rule scores above the best DecisionBound setting by a wide point margin (0.17 on the first set, 0.26 here), although the intervals overlap.
- A blocklist of authority topics finds every escalate case on this set (6 of 6) as it did on the first (11 of 11).

What did not hold up:
- The first-release statement that no setting scores above 0.41 is false for this set. The best DecisionBound setting scores 0.514, with an interval that overlaps always abstaining.
- Simple rules did better here than on the first set across the board (for example 0.60 against 0.39 for the same-tool rule). This set has a larger share of apply cases (12 of 35 against 19 of 66), so it is easier for rules that carry a decision when the target uses the same tool. The two sets are not equally hard, so scores should not be compared as if they were.

## What this shows and does not show

- The shipped DecisionBound policy never returns `apply` for a different repository. It misses all 12 apply cases in every setting. A one line rule (carry the decision when the target config uses the same tool) gets 9 of the 12. This is a design limit of the shipped policy on these labels.
- The best simple rule escalates every authority topic, which is rule Q1 of the annotation guide the annotators followed. Part of its lead comes from encoding the labelling rule, so it should not be read as an independent finding. It reads the case `family` field, which comes from topic matching on the source span, not from any label.
- Authority topics are over-represented by design. 14 of the 40 candidates were drawn from them, so rules and blocklists that target those topics are favoured.
- The scored set drops the five cases the annotators disagreed on, and some agreed cases were close calls. Scores on a set of the hardest cases would probably be lower.
- With 35 cases and four classes (the smallest has 4 cases), per class rates are very uncertain. The intervals of most systems overlap. This run can show that the shipped policy is not better than simple rules here. It cannot rank the simple rules against each other.
- Both annotators and the guide come from one model family and one author, so an independent human check could move any label.

## Reproduce

```bash
cd benchmarks/carrybench/real_v2_holdout
python3 -I ../real_v1/tools/fetch_pins.py --pins pins.csv --cases cases_v2_holdout_candidates.jsonl --clones-dir ./clones
python3 -I ../real_v1/tools/evaluate_baselines.py --cases cases_v2_holdout_v1.jsonl --clones-dir ./clones --mined config_facts_v2_holdout.jsonl --out results/baselines_v2_holdout.json
python3 -I ../real_v1/tools/evaluate_decisionbound.py --cases cases_v2_holdout_v1.jsonl --clones-dir ./clones --mined config_facts_v2_holdout.jsonl --pins pins.csv --out results/decisionbound_v2_holdout.json
```

The per case predictions are in the two result files.
