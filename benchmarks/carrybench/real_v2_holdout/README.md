# CarryBench-Real v2 holdout, candidate set

Status: UNLABELLED. This directory holds 40 candidate cases and nothing else that could act as an answer. There is no `label` field, no gold, no verifier note and no baseline or DecisionBound result. The case file header says so too.

## Rule for use

These cases must be labelled blind before any new policy is fixed. In order:

1. Label the 40 cases with the v1.1 guide (`../real_v1/guides/guide_v1_1.md` including its v1 and v1.1 rules), with at least two independent annotators who do not see each other's labels. Human annotators are preferred, since all v1 labels came from one model family.
2. Annotators must not read `selection_log_DO_NOT_SHOW_ANNOTATORS.jsonl`, `selection_shortlist_DO_NOT_SHOW_ANNOTATORS.jsonl`, the v1 labels, or any result file.
3. Freeze the labels and record the commit.
4. Only then score a policy. A policy, prompt or threshold that was tuned after seeing these labels cannot claim this set as a holdout.

The v1 scorers keep only cases that have a `label`, so they will report nothing on this file until labels are added, which is intended. Add `label`, `label_status` and `label_provenance` in the v1 style after adjudication and keep excluded cases in a separate file.

## Contents

| File | What it holds |
|---|---|
| `cases_v2_holdout_candidates.jsonl` | Header record then 40 cases in the v1 case format (`carrybench-real-case-v1`) with `label_status` set to `unlabelled_candidate`. Order is shuffled with the seed and carries no stratum information. |
| `pins.csv` | 77 pinned repositories in the v1 column layout. None is in `real_v1/pins.csv`. |
| `config_facts_v2_holdout.jsonl` | Mined config facts for those repositories, for the config lookup baseline. Pointers only. |
| `curation_v2_holdout.json` | The hand written task, paraphrases and scope conditions per shortlist id. Original text, written for this set. |
| `selection_log_DO_NOT_SHOW_ANNOTATORS.jsonl` | Structural stratum of each case. |
| `selection_shortlist_DO_NOT_SHOW_ANNOTATORS.jsonl` | The 118 shortlisted candidates before curation. |
| `tools/shortlist_candidates.py`, `tools/assemble_cases.py` | The seeded selection and the assembly step. They import the v1 miner and change nothing in `real_v1`. |

No third party text is stored. Pointers carry repository, pinned commit, path, line range, SPDX identifier, a paraphrase and the SHA-256 of the whitespace-normalised span. A scan for runs of seven or more consecutive words shared between a paraphrase and its span found none. Every repository is MIT, Apache-2.0, BSD-2-Clause or BSD-3-Clause by the miner's licence check, with no repository flagged for non-permissive licence text.

Verify the pointers from fresh clones:

```bash
cd benchmarks/carrybench/real_v2_holdout
python3 -I ../real_v1/tools/fetch_pins.py --pins pins.csv --cases cases_v2_holdout_candidates.jsonl --clones-dir ./clones
python3 -I ../real_v1/tools/mine_decisions.py verify --cases cases_v2_holdout_candidates.jsonl --clones-dir ./clones
```

Last run: 77 repositories fetched, 83 pointers, 0 mismatches.

## Selection method

1. Discovery. GitHub repository search for public, non-fork, non-archived repositories pushed after 2026-06-01 under five permissive licences and three languages (Python, TypeScript, JavaScript) in three star bands. About 3,800 repositories were found. Each was tree-scanned for agent instruction files (AGENTS.md, CLAUDE.md and similar) or ADR directories. 1,547 had one.
2. Exclusions. Any repository in `real_v1/pins.csv`, any repository whose licence text was not a single clean permissive licence, and any whose licence file mentioned a non-permissive licence. 1,378 repositories remained.
3. Mining. The v1 miner (`mine_decisions.py mine`) ran on all of them at one pinned commit each.
4. Source decisions. Statements from agent files, CONTRIBUTING files, ADRs and RFCs that name exactly one tool or practice (an atomic span of 30 to 380 characters). Tool statements must name the tool inside explicit choice wording, which removes plain command lists. Authority topics (AI disclosure, co-author trailers, DCO, CLA, security reporting, agent public actions, release publishing, licensing) were found by extra keyword patterns in `shortlist_candidates.py`.
5. Target stance. Rebuilt from the miner's config facts (lockfiles, config files, tool tables) and text statements in the target repository. Strata describe what the target holds, not which action is right: A target config uses the same tool, B target config uses a different tool, C only target text names a different tool, D target config and text disagree, E change record tooling, F practice with target text on the same practice, G authority topic with target text, H authority topic where keyword search found no target text (a guidance file pointer is given instead).
6. Draw. Seeded (20261117), at most two cases per source repository and per target repository, at most seven per topic family, with a few deliberate cross-ecosystem draws allowed in stratum B. The draw was oversampled three times to 118 shortlisted items.
7. Curation. One person read each shortlisted item and kept 40, dropping spans that were not a decision (command lists, fragments, headings), irrelevant target pointers, and a pair that shared a span with another case. Paraphrases, tasks and scope conditions were then written. No label was formed or recorded. One pointer (cbh-030, a target pull request flow section) was added by hand after a spot check showed the target does speak to the topic.

Result: 40 cases, 38 source and 40 target repositories (77 distinct). Strata: A 5, B 5, C 3, D 2, E 4, F 7, G 8, H 6.

## Known selection bias

- Recency. Repositories were chosen for recent pushes and for having agent instruction files, so the set over-represents projects from the agent coding era. Older projects with ADRs are rare (the pool had 31 ADR-only repositories before sampling).
- Popularity and language. The search covered Python, TypeScript and JavaScript only, and star bands, so large well-known projects and those languages dominate.
- Regex mining. English keyword patterns decide what counts as a statement, a tool and an authority topic. Spans in other languages and decisions phrased without those words are missed. Tool statements need choice wording, so decisions stated implicitly are underrepresented.
- Random pairing. Targets were drawn by structural relation, not by real contributor flows, so some pairs are artificial and many source and target repositories are unrelated projects.
- Stratum quotas are by design. They are not natural frequencies, so class shares here say nothing about how often each action is right in practice.
- Silent targets rest on a keyword search of guidance files. Absence of a match is not proof the target says nothing.
- Curation by one reader. Dropping spans that were not decisions is a judgement call and was made by a single reader with no second check.
- The v1 guide rules were written after seeing v1 disagreements. Labelling this set with them inherits those choices.
- Static pinned snapshots. Repositories keep changing.
