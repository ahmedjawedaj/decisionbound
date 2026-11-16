# CarryBench-Real executable checks

`carry_verifiers.py` holds 34 scenarios over 33 of the 66 cases: 17 apply, 12 abstain, 3 translate and 1 escalate. Each scenario is a small synthetic reduction of one case with a before state, a gold end state that follows the case label, a wrong end state that a mislabelled policy would produce, and a pure Python check.

Run: `python3 -I verifiers/run_verifiers.py`. It exits nonzero when a gold state fails, a wrong state passes, or a do-nothing state passes a check that needs work.

What passing shows: for these cases the label implies an end state that a script can tell apart from the opposite decision.

What passing does not show:
- The fixtures are written for this package from short case paraphrases. They are not copies of the target repositories and no third party tool runs. "A frozen-lockfile install passes" and "black passes" are approximated by structural proxies.
- It does not show that an agent can solve the real task.
- Coverage is uneven. Translate has 3 cases and escalate has 1. Most of the 11 escalate cases have no end state a script can check, because the right behaviour is to stop and ask. The one escalate scenario checks that the agent asked a human a real question and made no unsigned commit.
- Each scenario also carries extra wrong states and alternative correct solutions (88 and 46 in all) as regression tests. A separate reviewer attacked the first version and found many holes. What was fixed and what still lands is in `REVIEW_NOTES.md`. Read it before relying on any check.
- Seven scenarios accept a do-nothing state by design: c05, c06, c07 (non-breaking), n05, p02, p05 and p14.
