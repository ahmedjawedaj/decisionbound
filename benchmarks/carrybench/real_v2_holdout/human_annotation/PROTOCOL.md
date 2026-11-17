# Human annotation protocol, CarryBench-Real v2 holdout

Thank you for helping. No human has labelled any CarryBench case yet. The 35 scored holdout cases were labelled by two AI annotators of one model family, so every score so far is agreement with that pair. Your labels will show whether those AI labels hold up, and some may not. A disagreement is a useful result, not a failure.

## What you are labelling

40 cases. Each one gives a source pointer (repository, commit, file, lines), a target repository and commit, a one line task, and pointers to evidence in the target. You decide what an agent should do with the source decision in the target repository: `apply`, `translate`, `abstain` or `escalate`. The rules are in `GUIDE_v1_1.md`. Follow that guide exactly, including its v1 and v1.1 rules. Do not use any other version of the guide.

Plan for several hours. Every case means opening files in two repositories.

## Rules that keep the labels independent

1. Work only from this packet. Do not open the public DecisionBound repository folders `annotations/`, `results/`, or any of these files: `cases_v2_holdout_v1.jsonl`, `excluded_v2_holdout.jsonl`, `AGREEMENT_REPORT.md`, `HOLDOUT_RESULTS.md`, and the two files whose names end in `DO_NOT_SHOW_ANNOTATORS.jsonl`. They contain the AI labels, the AI notes or the scores.
2. Do not discuss any case with the other human annotator until you have both sent your file.
3. Do not use an AI assistant to decide a label or write a rationale. Using a search engine or the project documentation for a tool you do not know is fine.
4. Do not label from memory of a project. Open the pinned files.
5. If you have already seen any AI label for these cases, say so in the header of your file (`saw_ai_labels`). Still label them, and say which cases you remember.

## Set up

You need git and Python 3.9 or later.

```bash
python3 -I tools/fetch_pins.py --pins pins.csv --cases cases_v2_holdout_candidates.jsonl --clones-dir ./clones
python3 -I tools/mine_decisions.py verify --cases cases_v2_holdout_candidates.jsonl --clones-dir ./clones
```

The second command checks that every pointer still matches the pinned text. If one does not, stop and report the case. Do not guess.

## Label

Copy `labels_template.jsonl` to `labels_<your id>.jsonl` and fill it in. The first line is a header about you. Each following line is one case.

Fields for each case:

- `label`: `apply`, `translate`, `abstain`, `escalate`, `invalid` or `no_signal`. Use `invalid` or `no_signal` only as the guide says.
- `allowed`: optional. One more label you would also accept from a careful engineer.
- `confidence`: a number from 0 to 1.
- `strength`: `strong`, `medium` or `weak`, as the guide defines them.
- `deciding_tier`: `T1`, `T2`, `T3` or `T4`, the tier of the pointer that decided it. Use null for `invalid` and `no_signal`.
- `rationale`: one sentence in your own words, at most 400 characters. Do not paste source text.
- `translated_rule`: only for `translate`. Otherwise null.
- `invalid_reason`: only for `invalid`. Otherwise null.
- `read_beyond_pointers`: true if you had to read anything the pointers did not name, such as another file in the repository. Otherwise false.
- `notes`: if `read_beyond_pointers` is true, say which file and why. Also use it for anything unclear in the guide. The AI annotators needed to read beyond the pointers on several cases, and it changed some labels, so this field matters.

Check your file before sending it:

```bash
python3 -I tools/check_human_labels.py labels_<your id>.jsonl --cases cases_v2_holdout_candidates.jsonl
```

## Send it back

Send the checked file to the maintainer. It will be added to the repository as a new, clearly named set next to the AI labels. The frozen AI labels are never changed.

## What happens next

The maintainers compare your labels with the frozen AI labels and, if two humans take part, with each other. They report raw agreement and Cohen's kappa for the four way label and for the two way split (apply or translate versus abstain or escalate), and list every disagreement. Results are published as they come out, including if humans disagree with the AI labels on many cases.
