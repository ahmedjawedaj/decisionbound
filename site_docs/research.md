# Evaluation and research status

The code includes deterministic benchmark fixtures and an evaluation runner. These establish implementation behavior only; they do not demonstrate better coding accuracy, lower cost or production reliability than other tools. Any comparative claim requires properly licensed data, held-out cases, comparable budgets, documented baselines and reproducible evidence. No paper, thesis result or DOI is claimed by this alpha.

## Related work: harness engineering

A source-code study of eleven coding agent harnesses, including Claude Code, Codex CLI, Gemini CLI and OpenHands, was posted as [arXiv 2609.00006](https://arxiv.org/abs/2609.00006) (Barbaste et al., 15 July 2026). It defines an agent as a model plus a harness and reports 13 observations, 29 recurring patterns and 18 design recommendations. Its own limits matter here. It reads source code and did not run the systems on a shared task set, so it does not show that any pattern improves results. Its Claude Code analysis uses a March 2026 snapshot, and the authors say Claude helped draft the paper.

Findings that bear on this project:

- Verification in those harnesses is mostly done by outer loops and stop guards, and the authors note that a prompt asking for evidence is a request, not enforcement.
- Policy is moving from prompts into configuration and host-side checks.
- None of the studied harnesses retrieves code with vector embeddings or builds on a general agent framework.

How DecisionBound relates, checked against this repository:

- Already in place: completion is never claimed by a model, and `task_completed` stays false in every path we found. Review and export are bound to the patch hash and tampering tests exist. Skill and repository content is treated as untrusted data and skills load lazily. Context is selected under a token budget with no embedding retrieval.
- Gaps we found, now on the tracker: no early stop for repeated identical calls and no deadline in the read-only agent loop (#31), no explicit markers around untrusted tool output and observations cut in the middle of JSON (#32), and no single written list of fixed permission limits with the test behind each (#33).
- Out of scope for now: multi-agent orchestration beyond what exists, and an editor protocol server.

The catalogue has no pattern for deciding whether a decision remembered from one project should be applied, translated, abstained from or escalated in another. CarryBench measures that question, with the limits stated above: the labels so far come from AI annotators of one model family and no human has labelled any case. This section is context and makes no performance claim.
