# Agreement report: pilot v0, 35 cases

Status: PILOT. Both annotators are language models, so this is not human agreement. Their errors are likely to be correlated, which means the numbers below are probably higher than a human pair would reach. Do not cite them as validation.

## Setup
- Annotator 1: the pilot author (LLM assisted), labels written before this pass.
- Annotator 2: a separate agent that saw only the guide with worked examples removed, the unlabelled case file and the pinned clones. It reported no contamination. Pointer verification: 89 pointers, 0 mismatches.

## Result
- Four way label: 29 of 35 match, raw agreement 0.829, Cohen's kappa 0.734.
- Two way split (apply or translate versus abstain or escalate): raw 0.886, kappa 0.765.
- Annotator 1 counts: apply 16, abstain 14, translate 3, escalate 2. Annotator 2 counts: apply 18, abstain 9, translate 5, escalate 3.

## Disagreements (6)
| Case | A1 | A2 | What the split is about |
|---|---|---|---|
| tbr-c05 | abstain | translate | Changelog entry as a practice versus changelog tool as a choice |
| tbr-c07 | translate | apply | Same changelog question, target limits fragments to one type |
| tbr-c09 | abstain | translate | Package manager rule that reads as both a choice and a practice, target needs a carve out |
| tbr-c12 | abstain | escalate | AI disclosure, target silent on it |
| tbr-c17 | abstain | apply | Default export, target mandates it for consistency not necessity |
| tbr-c18 | abstain | translate | Barrel file, target text and actual code point different ways |

Five of six are cases where annotator 1 withheld and annotator 2 allowed some transfer. That pattern points at the guide, not at random noise.

## Guide ambiguities reported by annotator 2
1. AI disclosure sits under both Q1 (escalate on AI policy) and Q4 (translate example).
2. Package manager is a choice, yet the practice example is "never bypass the dependency manager".
3. Changelog tool is a choice but a changelog entry is a practice.
4. Order of Q3 and Q5 when the target speaks but a scope condition fails.
5. No tier for a stance implied only by file structure.

## Next steps
1. Write guide v1 that settles the five points by rule, before any adjudication.
2. Re-annotate only the six disagreements and any case touching an ambiguous rule, using the v1 guide, and record each adjudication with its rationale. Never settle a case by choosing whichever label is more convenient.
3. Keep the first annotator's original labels in the file so the pre adjudication agreement stays reproducible.
4. Add a human annotator when one is available. Until then every document says two LLM annotators.

## Update: guide v1 and two fresh blind annotators (B and C)

Guide v1 added five rules (atomic spans, choice versus practice test, AI policy handling, scope checked first against T1 state, T4 only means no_signal). Annotators B and C each labelled all 35 cases blind to each other, to annotator 1 and to the first blind pass. Both reported 0 pointer mismatches and no contamination.

| Pair | Raw agreement | Cohen's kappa |
|---|---|---|
| B vs C (both v1) | 0.943 | 0.916 |
| A1 vs A2 (v0 guide) | 0.829 | 0.734 |
| A2 vs B | 0.800 | 0.704 |
| A2 vs C | 0.800 | 0.704 |
| A1 vs B | 0.771 | 0.650 |
| A1 vs C | 0.743 | 0.607 |

B and C disagree on 2 cases (tbr-c17, tbr-p13). Those are marked contested and excluded. tbr-p15 is dropped as no_signal by both (only a 2018 revert supports it).

B and C agree with each other on 7 cases where annotator 1 had a different label (c07, c10, c12, c18, p10, p11, p15). All 7 original labels are kept in the file. The adjudicated label is the B and C label.

### Honest reading
- B and C share one guide and one model family, so their high agreement is partly agreement about how to read the rules. It is not proof the labels are correct.
- The v1 rules also move labels on purpose. R4 forces tbr-p10 to abstain because its source scope names Python and the target is TypeScript, even though both annotators said a careful engineer would likely apply. That case is flagged rule_sensitive and should not be used as a headline example.
- New ambiguities found: whether a descriptive scope item like "Python test suite" or an actor condition like "author writes the changeset" triggers R4 (p10, c05, c09), whether Q1 beats R4 for licences (c15), whether a true apply is allowed for AI policy (p11), and what counts as "file structure alone" in R5. These go into guide v1.1 and apply to new cases only. Pilot labels are not re-tuned to fit v1.1.
- Result: 32 of 35 pilot cases are agreed under v1, 2 contested, 1 dropped. Still two LLM annotators, no human.

## Update 2: 40 new cases under guide v1.1 (annotators D and E)

A builder agent assembled 40 new candidate cases from the pinned clones with no labels and no hints. Annotators D and E labelled them blind to each other and to everything above. Pointer check: 123 pointers, 0 mismatches. Both reported no contamination. One process note: annotator E found that /tmp/ann was shared between agents and a helper script there had been overwritten. It opened none of the other agents' output files. This is a limitation of the setup, not evidence of leakage, but a stricter run should use separate working directories.

- D vs E, four way: raw agreement 0.850, kappa 0.798. Two way (transfer or withhold): raw 0.925, kappa 0.850.
- Agreement is lower than the pilot B vs C result (0.943, kappa 0.916). The new cases are harder: 14 of 40 are authority topics and 18 are practices.
- 34 of 40 agreed, 6 contested and excluded: n02, n16, n18, n20, n26, n27.
- Agreed new labels: translate 10, abstain 10, escalate 8, apply 6.

### Combined candidate set
Pilot 32 agreed plus new 34 agreed gives 66 candidate cases: abstain 23, apply 19, escalate 11, translate 13. Translate and escalate are no longer thin.

### What the contested cases show
The six splits fall into three recurring questions the guide still does not settle:
1. Apply versus translate when only a trigger, scope or strictness changes (n16, n26, n27).
2. Whether a target that is stricter, looser or uses a different channel counts as a "position difference" at Q1 (n02, n20).
3. Choice versus practice for tools not in the R2 list, such as a mocking library (n18).
These are listed for guide v1.2. Agreed cases are not re-opened.

### Limits that remain
- Two LLM annotators of one model family agreeing is weaker evidence than two humans. No human has labelled any case.
- Labels were agreed under guide v1 and v1.1, which were written after seeing disagreements. The pilot labels were not re-tuned to v1.1.
- Since this report was written, baselines, guide-free model runs and executable end-state checks have been run. See BASELINE_RESULTS.md, DECISIONBOUND_RESULTS.md and verifiers/README.md. None of them changes the labels or adds a human annotator.
