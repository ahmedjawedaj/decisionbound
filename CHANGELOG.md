# Changelog

## Unreleased

- Rewrite the README around one 60-second offline demo (three projects, one approved decision, `apply`, `translate` and `abstain` outcomes, then a context capsule) with real output pasted from a run, an install that uses `pip install .`, and a plain status section that links CarryBench and says its labels are LLM-made with no human labels. The Pages quickstart now carries the same demo, a correct clone URL, and notes on confusing output (`approve` returns revision 2, `write_consent: false` is informational). Fix stale statements in the docs index, SDK page and `src/decisionbound/README.md`.
- Add `tests/integration/test_docs_quickstart.py`: runs the README and Pages demo from the installed package and compares output with the documented blocks, checks that every `dbound` command and option in the docs exists, that documented pip extras exist, that README links resolve, that the example scripts and workflow manifests run, and runs the SDK example.
- Add CarryBench-Real v1 under `benchmarks/carrybench/`: 66 real cases from 50 permissively licensed repositories as pinned pointers and hashes (no third-party text), labelled by two blind LLM annotators with no human labels, with a dataset card, agreement report, eight simple baselines, scores for the shipped transfer methods, two guide-free language model runs, 33 executable end-state checks on synthetic reductions, and fetch, mine, verify and scoring tools. The set is unvalidated and small, and every document says so.
- Fix the package `__version__` (it said 0.6.0a1) and add a test that keeps it equal to `pyproject.toml` and `CITATION.cff`.
- Add a README section on closely related decision-memory work, and say plainly that the bundled fixtures are synthetic.
- Add worker-process containment (`decisionbound.containment`): `ContainedWorker` runs tasks in a killable process tree with a hard deadline, an allowlisted environment, bounded single-line receipts and optional resource limits. The containment tests pass in CI on Linux, macOS and Windows (Python 3.11 to 3.13). Open review items remain for Windows, listed in the containment page.
- Add `ProcessRegistry`, a lazy additive `contained_processes` table (schema version stays 2), and `OrphanReaper`, which kills only processes whose start token matches.
- The supervisor now creates an `ExecutionControl` per lease, ticks every `min(0.5, lease/3)` seconds, cancels workers on a revoked claim, stop grace expiry or lost leadership, and records `late_result_rejected` events. `WorkflowWorker.execute_controlled` defaults to `execute`, so existing thread workers behave as before.
- The coordinator reaps orphans right after acquiring leadership and reports `orphans_reaped` and `orphans_unconfirmed`. New setting `worker_stop_grace_seconds`. New command `dbound workflow reap-orphans`.
- New quarantine reason `containment_failed` and journal helpers `note_late_result`, `record_containment_event`, `block_uncontained` and `claim_is_current`.
- Gate G17 is partially met, not closed.

## 0.13.0a1 — experimental alpha

Local decision records and transfer policies; bounded read-only agent execution; consent-bound coding and review; SQLite workflow coordination, backups and migration; loopback web API; read-only MCP and editor clients; reproducible package tooling.

Platform, isolation and security limitations are documented in the user guides and SECURITY.md. No stable release or production certification is claimed.
