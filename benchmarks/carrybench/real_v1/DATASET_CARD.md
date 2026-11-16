# CarryBench-Real v1 dataset card

CarryBench-Real is a small set of real cases for one question: when a human has approved an engineering decision in repository A, what should an agent do with it in repository B?

Status: first release, 66 labelled cases, unvalidated. Labels come from two blind language-model annotators of one model family. No human has labelled any case. Agreement between those two annotators is not evidence that the labels are right. Use this set to compare policies against each other and to find where a policy is unsafe, not to claim a measured accuracy against human judgement.

## The task

Each case gives a decision recorded in a source repository, a task in a target repository, and pointers to what the target repository says on the topic. A policy must choose one of four actions.

| Label | Meaning |
|---|---|
| apply | Carry the decision over unchanged. |
| translate | Carry the intent over but change the form, for example a different tool for the same goal. |
| abstain | Do not carry it over. The target has its own stance or the decision does not fit. |
| escalate | Stop and ask a human, because the topic needs a person's authority (licence, security reporting, public actions, AI disclosure). |

Evidence is ranked in tiers. T1 is enforced state (lockfile, config, CI). T2 is a recorded decision (ADR, agent instruction file). T3 is guidance prose. T4 is history. The annotation guides in `guides/` define the questions and rules the annotators followed. Guide v1.1 applies to the 34 newer cases and guide v1 to the 32 pilot cases. Pilot labels were not re-tuned to v1.1.

## Contents

| File | What it holds |
|---|---|
| `cases_v1.jsonl` | One header record then 66 cases. Label counts: abstain 23, apply 19, translate 13, escalate 11. |
| `excluded_v1.jsonl` | 9 cases kept out of scoring: 8 where the two annotators disagreed and 1 with no usable signal. Raw labels are shown so nothing is hidden. |
| `annotations/` | Every annotator's labels with rationale, and the adjudication records. The pilot author's first labels are kept so agreement before adjudication can be reproduced. |
| `guides/` | The three versions of the annotation guide. |
| `pins.csv` | 73 pinned repositories: URL, commit, SPDX licence, which signal files exist. |
| `AGREEMENT_REPORT.md` | Agreement statistics and the problems found along the way. |
| `BASELINE_RESULTS.md`, `results/` | Scores of eight simple baselines. |
| `tools/` | `fetch_pins.py`, `mine_decisions.py`, `evaluate_baselines.py`. Standard library plus git. |

The cases span 32 source repositories and 42 target repositories, 31 topic families, and Python and TypeScript projects.

## Case record

Fields: `case_id`, `family` (the topic), `source` (repository, pinned commit, path, line range, SPDX licence, a short paraphrase, scope conditions), `target` (repository, pinned commit, the task, and evidence pointers), `label`, `label_status`, `label_provenance`, `verifier_note` (what an end state check would test) and, on one case, `rule_sensitive`.

Evidence pointers carry a path, a line range, a SHA-256 of the whitespace-normalised span and a paraphrase. They do not carry the source text. To read the text, fetch the pinned commit.

## Provenance and licences

Every repository is public and permissively licensed (MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, MPL-2.0 or a dual MIT and Apache-2.0 licence), read at a pinned commit. The dataset stores no third-party text: only pointers, hashes and short paraphrases written for this set. A scan for runs of seven or more consecutive words shared with the source text found none after paraphrases were reworded. Re-check with `python3 -I tools/mine_decisions.py verify`, which re-hashes every pointer against the pinned clones: 188 pointers in the scored file and 24 in the excluded file, 0 mismatches.

Original material in this package is Apache-2.0 like the rest of the repository. The pinned repositories keep their own licences.

## How the labels were made

Two blind annotators labelled each case without seeing each other's labels, the first author's labels or any score. Each reported zero pointer mismatches and no contamination. Annotators ran in agent sessions that shared one scratch directory, which is a setup limitation and not evidence of leakage; a stricter run would separate them. A case enters the scored file only when both agree. Contested cases are excluded and listed.

| Set | Cases | Annotators | Raw agreement | Cohen's kappa |
|---|---|---|---|---|
| Pilot, guide v1 | 35 | B and C | 0.943 | 0.916 |
| New, guide v1.1 | 40 | D and E | 0.850 | 0.798 |

The two-way split (transfer versus withhold) is also reported in `AGREEMENT_REPORT.md`.

## Known limits

1. No human annotator. All four blind annotators are instances of one model family. Their errors are correlated, so agreement is probably higher than a human pair would reach.
2. Guides were written after seeing disagreements. The rules therefore encode choices that other reasonable people could make differently. Open guide ambiguities are listed in the report and would go into a v1.2 for future cases.
3. Exclusion bias. Contested cases are the hard ones, and they are removed from scoring. Scores on this set are likely higher than scores on all cases would be.
4. Small. 66 cases, an average of two per source repository. Confidence intervals are wide.
5. One case, tbr-p10, is rule sensitive. Its label depends on a rule that forces abstain when a named language scope is not met, although both annotators said a careful engineer might apply. Do not use it as a headline example.
6. Static. Labels describe a repository at one pinned commit. Repositories change.
7. Verifier notes describe what an end state check should test. Executable checks exist for 33 of the 66 cases (see `verifiers/README.md`). They run on small synthetic reductions, run no third party tool, and cover only one escalate case.
8. Not a safety benchmark of any particular agent product. It tests a decision policy, not a model.

## Intended and out-of-scope use

Intended: comparing policies for carrying decisions between repositories, with attention to harmful apply (applying where a human decision was needed) and missed transfer.

Out of scope: claiming human-validated accuracy, ranking models without a human-labelled holdout, training a model on these cases and then reporting on them, or treating a label as a recommendation for a specific real repository.

## Adding results

Report four-way accuracy, macro F1, harmful apply rate and missed transfer rate with a bootstrap that resamples source repositories, as `tools/evaluate_baselines.py` does. State which labels you scored on and the dataset version. Submissions that tune on this file should say so. A leaderboard by pull request is planned only once a human-labelled holdout exists.

## Citation

Cite the repository and the pinned release commit. A paper draft is not yet public, so do not cite one.
