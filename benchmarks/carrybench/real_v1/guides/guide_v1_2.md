# CarryBench-Real annotation guide v1.2 (DRAFT, new cases only)

Status: draft. It has not been used to label any case. Guide v1.1 stays in force for the 66 v1 cases and the 35 scored v2 holdout cases. Nothing here may be applied back to them.

Provenance: this draft was written after reading the two blind annotators' process notes on the v2 holdout (`annotations/notes_A.md` and `notes_B.md`). It therefore cannot be evaluated on that holdout. Any claim about v1.2 needs a fresh set whose candidates are committed unlabelled before the labels are produced.

Everything in guide v1.1 applies unless a rule below says otherwise.

## What the v2 holdout notes showed

Both annotators agreed on 35 of 40 cases. Between them they named six places where the guide gave no single answer. Each rule below settles one of them.

## v1.2 rules

R7. Mixed spans, ordering of evidence. When a span states more than one atom, pick the atom in this order. First, the atom the case paraphrase names. Second, if the paraphrase names both, the atom the case `scope_conditions` list. Third, if both atoms would give the same label, label that. Only if the atoms would give different labels and nothing above separates them, mark the case `invalid` with reason `mixed_span`.

R8. Practice with an availability scope versus R4. A scope condition only triggers R4 if it is an ecosystem condition in the sense of R4a. A condition such as "when the tool is available" or "when the target uses a changelog tool" is an ecosystem condition if the target's T1 state shows the tool absent. In that case label `abstain`. If the target's T1 state is silent about the tool, do not use R4. Continue at Q4 and treat the span as a practice.

R9. Language conditions. A language condition triggers R4 in two cases. The span's own wording names the language. Or the span names constructs that exist only in one language (for example `any`, `unknown` and `object` as TypeScript types) and the case `scope_conditions` list that language. A span that only sits inside a language-specific file does not trigger it, as in R6.

R10. Same position, different channel. This applies to Q1 topics such as security reporting and licence headers. Compare the obligation first and the channel second.
- Same obligation and the same kind of channel: label `apply`.
- Same obligation, and the named channel does not exist in the target but the target names its own: label `translate`, and write the target's channel in the translated rule.
- Same obligation, and the target names no channel or two conflicting channels: label `escalate`.
- Different obligation: label `escalate`.

R11. Pointers that only show a path. When the only target evidence for a case is a `path_present` pointer, you may read neighbouring files in the target repository at the pinned commit to find the stance. If you do, record `read_beyond_pointer: true` and list the files you read. The strength of that label is at most `medium`. A label that rests only on the path being present is `no_signal`, as in R5a.

R12. Local reading of the pinned span. Read the whole section that contains the pinned lines before you label. If a nearby line in the same file limits or reverses what the pinned lines say, the limiting line decides, and the rationale must name it. (The v2 holdout case cbh-030 is the reason for this rule: the pinned lines describe auto approval, and lines a few rows later exclude fork pull requests.)

## Changes to how cases are built, for the next set

These do not change how a case is labelled. They change what a case must contain before it reaches an annotator.

B1. Config pointers must carry a text hash of the pointed line. In v2 a config pointer had a null text hash, so `verify` passed even though the line number was wrong (cbh-006 points at line 330 and the setting sits at lines 342 and 343).
B2. A case whose only target pointer is `path_present` should be built with at least one text pointer that carries the stance, or it is excluded before annotation.
B3. Selection logs and shortlists stay out of the annotators' folder, as in v2.
B4. The candidate file is committed unlabelled and the commit time is recorded before any annotator output is produced.
B5. Report the share of cases on authority topics (Q1) next to every score, because that share changed the difficulty between v1 and v2.

## What this draft does not do

It does not add a human annotator and it does not add a second model family. Both remain the main open limits of CarryBench. A human pass over the v2 holdout cases is still the most useful next step, and a human should read v1.2 before it is frozen.
