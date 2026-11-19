# AI draft labels from another model family (not human, not verified)

This folder holds one extra set of labels for the 40 holdout candidates. It is an AI draft. It is not a human label set, it is not an input to the frozen scored set, and it does not change the frozen labels, the case files or any result. Report it separately.

| Item | Value |
|---|---|
| File | `labels_gpt6_ai_draft_v1_1.jsonl` |
| SHA-256 | `9f3670fa87c117cccdd98a99d1ade9f814349a72876bb8d45e54633e5ee74bdd` |
| Annotator id in the file | `gpt6_ai_draft` |
| Guide | v1.1 |
| Cases | 40 (all candidates) |
| Supplied by | the repository owner, filed unchanged on 2026-11-19 |
| Model family | `gpt6` in the id, as stated by the owner and not verifiable from the file |

## What is known about how it was made

- The file header says it was generated from the packet paraphrases only, with no pinned-file verification, and that it is not human. Every row repeats this in its `notes`, and `read_beyond_pointers` is false on all 40. So the file states that the pinned source and target files were not opened. This is a self report and was not audited. A spot check of five rationales found nothing beyond what the packet paraphrases contain.
- The header also says `saw_ai_labels: false`, which is also a self report and was not audited. The model, its serving version, the prompt and the order of work are not recorded beyond the annotator id, so the run cannot be reproduced and the model family rests on the owner's word.
- Because the frozen annotators A and B read the pinned files, this draft is weaker evidence than they are. Some of its disagreements may come from labelling a short paraphrase and not the file.

## Agreement with the frozen AI labels

Computed with `human_annotation/tools/compare_labels.py` and checked by `tests/unit/test_carrybench_ai_draft.py`.

| Compared with | Cases | Raw agreement | Cohen's kappa |
|---|---|---|---|
| Annotator A, all 40 cases | 40 | 30/40 (75.0%) | 0.66 |
| Annotator B, all 40 cases | 40 | 29/40 (72.5%) | 0.63 |
| Frozen scored set, four labels | 35 | 27/35 (77%) | 0.69 |
| Frozen scored set, carry versus stop | 33 | 28/33 (85%) | 0.69 |

Two pairs are left out of the carry versus stop line because the draft used `invalid`. The frozen pair agreed with each other on 35 of 40 (0.875, kappa 0.83), so the draft sits below that level. This is an AI against AI comparison. It does not show that any label is correct.

Label counts. Draft on all 40 cases: abstain 13, apply 11, escalate 11, translate 3, invalid 2. Draft on the 35 scored cases: abstain 10, apply 10, escalate 10, translate 3, invalid 2. Frozen scored labels: abstain 13, apply 12, escalate 6, translate 4. The draft escalates more often than the frozen pair.

## The eight disagreements on the scored cases

| Case | Frozen | Draft | Pattern |
|---|---|---|---|
| cbh-001 | abstain | invalid | Mixed span, the draft says the atom cannot be told (guide ambiguity 2) |
| cbh-021 | abstain | invalid | Same mixed span reason |
| cbh-003 | translate | apply | The target has a contributor agreement mechanism of its own |
| cbh-009 | apply | escalate | Security reporting channel, the kind of case guide ambiguity 4 describes |
| cbh-011 | translate | escalate | Same kind of security reporting case |
| cbh-020 | apply | escalate | The pointer only shows a file exists, the draft did not read sibling files |
| cbh-030 | apply | escalate | Agent public action, the draft held that a person must approve |
| cbh-032 | abstain | translate | The target discourages console output in examples only |

Three of the eight (cbh-003, 011 and 020) are cases the frozen annotators listed under a guide ambiguity in `AGREEMENT_REPORT.md`: number 4 for cbh-003 and 011, number 5 for cbh-020. That is some evidence that part of the gap there comes from the guide and not from the model family. It is not a test of that. Cbh-001 and 021 are listed under ambiguity 1, but the draft's reason for `invalid` is the mixed span question of ambiguity 2, which lists no cases, so ambiguity 1 does not explain those two.

On the five contested cases the frozen pair did not agree on, the draft chose apply (cbh-017), abstain (cbh-025, 028 and 034) and escalate (cbh-031). These were not adjudicated and are not used to change the excluded set.

## Rules for using this file

- Never merge it with the human labels or use it to adjudicate between human annotators.
- Do not show it to a human annotator before their own labels are in. A person who has read it must set `saw_ai_labels` to true.
- The label checker accepts it because its format is valid. Passing the checker does not make it a human file, and `compare_labels.py` prints its declared background so the difference is visible.
