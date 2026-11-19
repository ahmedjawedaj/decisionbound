# Agreement report, CarryBench-Real v2 holdout

Status: labels frozen. No policy, baseline or DecisionBound score had been computed on this set when the labels were frozen. The freeze commit comes before any results file.

The two annotators are AI agents of one model family, labelled A and B. No human has labelled any case. They worked from the v1.1 guide only, in separate folders, with the pinned clones and no access to each other's labels, the selection logs, the v1 labels or any result. Annotator B processed the cases in reverse order to reduce order effects. Both ran the pointer check first and found 83 pointers and 0 mismatches. Both report using no memory of the projects. These are self reports from the annotators and were not independently audited.

## Agreement

| Measure | Value |
|---|---|
| Cases | 40 |
| Raw agreement on the primary label, all categories | 35 of 40 = 0.875 |
| Cohen's kappa, all six categories | 0.83 |
| Cases where both used one of the four labels | 38 |
| Raw agreement and kappa on those 38, four labels | 0.921 and 0.889 |
| Raw agreement and kappa on those 38, carry (apply or translate) versus stop (abstain or escalate) | 0.947 and 0.894 |
| Scored cases (both agree on one of the four labels) | 35 |
| Excluded cases | 5 |

Scored label counts: abstain 13, apply 12, escalate 6, translate 4. Always abstaining would match 13 of 35 = 0.371.

## How to read this

- A and B share one guide and one model family, so agreement partly measures agreement about how to read the rules. It is not evidence that the labels are correct, and it would probably be lower for a human pair.
- Guide v1.1 was written after the first release, from ambiguities seen in it, so the rules are fitted to that kind of case. This set tests those rules on new repositories. It does not test them independently.
- Many agreed labels are close calls. In 12 of the 35 scored cases at least one annotator reported confidence of 0.6 or lower (cbh-002, 003, 008, 011, 014, 021, 023, 026, 030, 032, 033, 036). In 17 of the 35 at least one annotator named an acceptable second label. Mean reported confidence on the scored cases was 0.76 for A and 0.77 for B. Reported confidence is a model's own estimate and is not calibrated.
- Excluding the five contested cases removes the hardest ones, so scores on the remaining 35 are probably optimistic for any system.
- The smallest class has 4 cases, so any per class rate is very uncertain.

## Contested cases, excluded

Raw labels are kept in `excluded_v2_holdout.jsonl`. Nothing was adjudicated.

| Case | A | B | Why they differ |
|---|---|---|---|
| cbh-017 | apply | invalid, mixed span | The span states a test runner pick and a separate pre-commit practice. A labelled the runner atom, B judged the atoms would label differently and marked it invalid. |
| cbh-025 | abstain | translate | The span never names a language. A used the listed TypeScript condition and the target's Python project to abstain. B followed the span wording and found a Python counterpart rule. |
| cbh-028 | abstain | no_signal | The target forbids mocking the database only. A abstained, B found nothing that speaks to the practice. |
| cbh-031 | abstain | escalate | The target agent file says nothing about git limits. A read it as not carrying over, B read it as a silent agent permission topic. |
| cbh-034 | translate | abstain | The target forbids adding any dependency in that package. A saw the same minimising intent in another form, B saw nothing to carry over. |

## Guide ambiguities both annotators reported

1. R4 (a failed scope condition forces abstain) and R2 (recording a change or using a tool is a practice, so translate) point to different labels when a practice is phrased with a tool that is not available in the target. Both followed R4 as written (cbh-001, 002, 021, 036). This was not tested against R2.
2. Mixed spans. The guide says to mark `invalid` when the atom cannot be told, but does not say whether the listed `scope_conditions` may be used to pick the atom. A used them, B used `invalid` only when the atoms diverge.
3. R6 says a language is a condition only when the span states it. The case file lists a language in scope conditions anyway in some cases (cbh-025, 028). The guide does not say whether the listed conditions or the span wording control.
4. R3 and R3a cover AI and contributor policy only. For security reporting and licence headers, the line between translate and escalate for "same position, different channel" is not defined (cbh-003, 011, 026, 033).
5. Pointers that only show a file exists (cbh-004, 014, 020, 023, 026, 031). R5 and R5a do not say whether to read sibling files. Both annotators did, so these cases were labelled from files the pointer does not name.

These feed a guide v1.2 for new cases only. Nothing here was changed in this set.

## A further AI draft from another model family

One more AI label set exists for these 40 cases, made from the packet paraphrases only, without opening the pinned files. It is filed apart in `annotations/ai_other_family/` and is not part of the frozen scored set. It agrees with the frozen scored labels on 27 of 35 cases (kappa 0.69). See `annotations/ai_other_family/README.md` for provenance, limits and the eight disagreements. It is not a human label set.

## Known data issue

In cbh-006 the config pointer names `pyproject.toml` line 330, but the Google pydocstyle setting sits at lines 342 and 343. Line 330 shows an unrelated ignore entry. The config fact carries no hash, so the verifier cannot catch this. Both annotators read the right lines and agree on the label. The pointer is left unchanged so that the frozen set matches what the annotators saw. Fix it in a later revision with a version bump. Issue 21 audited all ten pointers of this kind. `errata_v2_holdout_v1.jsonl` records the hash of the right lines for cbh-006 and a second off pointer, cbh-039 (line 29 is the `format` script and the `oxfmt` dependency is at line 69). Neither changes a label.

## Reproduce

```bash
python3 -I tools/freeze_labels.py   # rebuilds cases_v2_holdout_v1.jsonl and excluded_v2_holdout.jsonl
```

Annotator records are in `annotations/` with their process notes.
