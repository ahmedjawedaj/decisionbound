# CarryBench-Real annotation guide v1.2 (DRAFT, new cases only)

Status: draft. It has not been used to label any case. Guides v1 and v1.1 stay in force for the cases they labelled, as recorded in the dataset card and the v2 holdout README. Nothing here may be applied back to those cases.

Provenance: this draft was written after reading the two blind annotators' process notes on the v2 holdout (`annotations/notes_A.md` and `notes_B.md`) and the guide ambiguities listed in `AGREEMENT_REPORT.md`. It therefore cannot be evaluated on that holdout. Any claim about v1.2 needs a fresh set whose candidates are committed unlabelled before the labels are produced. It has not been tried by anyone, so it may contain conflicts that only labelling will show.

Everything in guide v1.1 applies unless a rule below says otherwise.

What the annotators receive: under v1.2, annotators get the case paraphrase and the listed `scope_conditions` along with the pointers. The v2 annotators did. The v1.1 text says the second annotator does not receive the first annotator's paraphrases, which is a different thing from the curator's case paraphrase.

## What the v2 holdout notes showed

The two annotators agreed on 35 of 40 cases. `AGREEMENT_REPORT.md` lists five guide ambiguities that one or both of them reported. R7 to R11 propose a rule for each. Annotator A also noted one pitfall (case cbh-030) that B did not mention. R12 addresses that one.

## v1.2 rules

R7. Mixed spans, ordering of evidence. When a span states more than one atom, pick the atom in this order. First, the atom the case paraphrase names. Second, if the paraphrase names both, the atom the case `scope_conditions` list. Third, if both atoms would give the same label, label that. Only if the atoms would give different labels and nothing above separates them, mark the case `invalid` with reason `mixed_span`.

R8. A practice or choice with an availability scope. This rule changes the v1.1 outcome for some cases. Both v2 annotators followed R4 for them, and under R8 they go to Q4 instead. Steps, in order:
1. Apply the R2 test to the atom. It is a choice or a practice.
2. A choice whose condition fails against the target's T1 state gets `abstain` (R4). A condition such as "tool X is available" fails when T1 shows a different tool of the same kind configured (for example a Poetry section in `pyproject.toml` for a "use uv" decision). If T1 says nothing about the tool, continue at Q2 and Q3 without R4.
3. A practice does not go through R4 for an availability condition. Go to Q4: `translate` if the target has its own mechanism for the same intent and does not forbid the practice, otherwise `abstain`.
R4a still holds. A condition about who acts or when ("the author writes the changeset") is never an R4 trigger.

R9. Language conditions. A language is an ecosystem condition in two cases. The span's own wording names it. Or the case `scope_conditions` list it, whatever the span says. The second case overrides R6 for listed conditions. Check the language against the target's T1 state. If it fails, R4 applies. Do not infer a language from which names a span uses, because names such as `any` or `object` exist in more than one language.

R10. Same position, different channel. This applies to security reporting. R3 and R3a keep governing AI and contributor policy, and Q1 keeps governing licence topics (a different licence or header rule is `escalate`, because there is no channel to translate).
- Same obligation means the target requires at least what the source requires. If the target requires less than the source, label `escalate`.
- Same obligation, and the source names no repository-specific channel ("report privately, not in a public issue"), and the target says the same: `apply`.
- Same obligation, and the source names a specific address or page that the target replaces with its own: `translate`, and write the target's channel in the translated rule. This follows the v1.1 tie-break that a changed location is `translate`.
- The target names no channel or two conflicting channels: `escalate`.

R11. Pointers that only show a path. This rule applies only when the sole target pointer for a case is `path_present`. Step 1: read neighbouring files in the target repository at the pinned commit. If text there states the stance, label from it, record `read_beyond_pointer: true` with the files read, and cap the strength at `medium`. Step 2: if the neighbouring files are also silent, do not label from the path alone. For Q1 topics follow R3 and R3a (`escalate`). For anything else mark `no_signal`. For path-only pointers, R11 overrides R5.

R12. Read the whole section of the pinned lines. If a nearby line in the same file limits or reverses what the pinned lines say, the limiting line decides, and the rationale must name it. (Case cbh-030 is the reason for this rule. The pinned lines describe auto approval of some pull requests, and lines a few rows later exclude fork pull requests.)

## Changes to how cases are built, for the next set

These do not change how a case is labelled. They change what a case must contain before it reaches an annotator.

B1. Config pointers must carry a text hash of the pointed line. In v2 a config pointer had a null text hash, so `verify` passed even though the line number was wrong (cbh-006 points at line 330 and the setting sits at lines 342 and 343).
B2. A case whose only target pointer is `path_present` should be built with at least one text pointer that carries the stance, or it is excluded before annotation.
B3. Selection logs and shortlists stay out of the annotators' folder, as in v2.
B4. The candidate file is committed unlabelled and the commit time is recorded before any annotator output is produced.
B5. Report the share of cases on authority topics (Q1) next to every score. The v2 holdout has 14 of 40 candidates on those topics by design, and the sets differ in difficulty.

## What this draft does not do

It adds no human annotator and no second model family. Both remain the main open limits of CarryBench. A human should read v1.2 before it is frozen.

It does not fix the existing labels. Four scored v2 holdout cases (cbh-004, 020, 023, 026) rest on path-only pointers that the annotators read beyond. Annotator A also noted reading beyond the pointer in cbh-014, which has a text pointer. Those labels came from files the pointers do not name, and the holdout results should be read with that in mind.
