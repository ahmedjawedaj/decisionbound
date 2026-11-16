# CarryBench-Real annotation guide v1 (blind edition, no worked examples)

This guide is for a second annotator who has not seen the first annotator's labels. The six worked examples come from the pilot. Their labels are PROPOSED by one annotator and are not validated. Treat them as illustrations of the procedure, not as ground truth.

## What you receive

For each case: a source pointer (repo, SHA, path, lines), a target repo and SHA, a one-line task, and a list of target evidence pointers. You do not receive the first annotator's label, rationale, confidence or paraphrases.

Set-up: clone each repository at the pinned SHA and open the pointed lines. Run `mine_decisions.py verify` first. If a hash does not match, stop and report the case. Do not label from memory of the project.

## Procedure

Answer the questions in order. Stop at the first one that gives a label.

**Q0. Is the source span a decision?** It must be normative, sit in an ADR, agent file, RFC or CONTRIBUTING file, and not be superseded, rejected or only proposed. If not, mark the case `invalid` with a reason. `invalid` is not one of the four labels.

**Q1. Does acting on it need authority an agent does not have?** Licensing terms, security posture, contributor or AI policy, anything that posts publicly on someone's behalf, or anything where the target says maintainers decide. If the target's position on such a point differs from the source or is unclear, label `escalate`.

**Q2. What is the target's stance for this task path?** Read every evidence pointer. Rank them:

1. T1 enforced state: lockfile, config table, CI workflow
2. T2 recorded decision: accepted ADR or agent file. The file nearest to the task path wins
3. T3 guidance prose: CONTRIBUTING, README
4. T4 history: revert commits

Higher tier wins. Current state beats history. If two pointers of the same tier disagree and nearness does not settle it, label `escalate`.

**Q3. Does the target stance agree with the decision, and do the scope conditions hold?** If yes, label `apply`. Small differences in command spelling do not matter if the same tool and rule are meant.

**Q4. The stance differs. Is the decision a choice or a practice?**

- A choice picks one member of an exclusive set: package manager, test runner, formatter, merge mode, changelog tool. The target has already chosen. Label `abstain`.
- A practice is a way of working that happens to be phrased with the source's tool: "never bypass the project's dependency manager", "disclose AI use". If the target offers its own mechanism for the same intent and does not forbid the practice, label `translate` and write the translated rule in one sentence. If the target forbids the practice itself, label `abstain`.

**Q5. Nothing in the target speaks to it.** If a scope condition visibly fails (different language, different licence, different toolchain), label `abstain`. Otherwise mark `no_signal`. Such cases are dropped from v0.

## Tie-breaks

- `translate` versus `abstain`: if you cannot write the translated rule without inventing a target convention that no pointer supports, it is `abstain`.
- `translate` versus `apply`: if only the command launcher changes (for example `npx` versus `pnpm exec`), it is `apply`. If the artefact, location or trigger condition changes, it is `translate`.
- `escalate` is not a way to avoid a hard call. Use it only for Q1 topics or same-tier contradictions.
- Keyword overlap proves nothing. Two files that both mention the same tool can state opposite rules.
- Give a confidence from 0 to 1 and a strength: strong (T1 or T2 text on point), medium (T3 or needs inference), weak (T4 only, or config that only implies the stance).
- You may add one extra label to `allowed_actions` if you would accept it from a careful engineer. Agreement is computed on the primary label.

(Worked examples removed for the blind pass so that no first-annotator labels leak.)

## What to record

`label`, optional second `allowed` label, `confidence`, `strength`, the tier of the pointer that decided it, and one sentence of rationale in your own words. For `translate`, the translated rule. Never paste source text into the record.

## Agreement reporting

Report raw agreement and Cohen's kappa for the four-way label and for the two-way split (apply or translate versus abstain or escalate). List every disagreement with both rationales. A case enters a release only after two matching primary labels or a recorded adjudication.

## v1 rules (these settle five ambiguities found in the first blind pass, and override anything above that conflicts)

R1. Atomic spans. A source span states one atom. If a span mixes a tool choice and a practice, annotate the atom the case paraphrase names. If you cannot tell which atom it names, mark the case `invalid` with reason `mixed_span`.

R2. Choice or practice test. Replace the tool or value named in the span with a placeholder. If the span still states a rule ("never bypass the dependency manager"), it is a practice. If it stops making sense or only says which one to use ("use X", "X is the toolchain"), it is a choice. Package manager, test runner, formatter, merge mode and changelog tool as a named pick are choices. A requirement to record a change (for example add a changelog entry) is a practice.

R3. AI use and contributor policy. Q1 always applies first. If the target has a recorded mechanism for the same obligation and does not conflict with it, label `translate`. If the target is silent, hostile, or only implies a stance, label `escalate`.

R4. Order of Q3 and Q5. Check scope conditions first, against the target's T1 state (lockfile, config, CI). If a scope condition visibly fails there, label `abstain` whatever the target prose says. Only then continue to Q2 and Q3.

R5. Stance from file structure alone is T3 weak at best, never T1 or T2. If the only target evidence is T4 history, mark `no_signal`. Do not label `apply` from history alone.
