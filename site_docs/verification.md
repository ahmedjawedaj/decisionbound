# Verification, threat model and recovery tests

DecisionBound does **not** consider a model's final message proof of user-task completion. The current runtime deliberately separates three concepts:

- **Preparation verified:** offline context was assembled under a budget.
- **Evidence inspection verified:** the read-only worker returned at least one parseable, tool-labeled observation.
- **Task completed:** always **false** for this alpha. A new code-write artifact/test path exists in ; its acceptance checks verify bounded patch/test receipts, **not** semantic task completion.

## Independent verifier boundary

`ReadOnlyLedgerVerifier` implements the `TaskVerifier` override contract. It compares parsed observations with SHA-256 tool receipts in the authoritative event ledger, and rejects missing, forged or truncated evidence. It does **not** certify the truth of model-synthesized prose.  adds `CodeReceiptVerifier`: it checks Git patch hashes, changed paths, test receipts and post-test diff integrity. Real container-test acceptance is distinct from semantic task completion. Mock runs can never certify acceptance.

## Threat-driven checks

| Threat | Current response | Test location |
|---|---|---|
| Prompt-injected tool output | Returned as untrusted data; host tool allowlist enforced | `tests/unit/test_model_planner.py` |
| Tool asks for write | Host refuses side-effecting tool even if model requested it | `tests/unit/test_core.py` |
| Path traversal or symlink | Repository tools reject absolute/parent traversal, hidden and symlinked paths | `tests/security/` |
| Missing credential / implicit network | Providers cannot run without `enabled=True` and env key | `tests/unit/test_provider_adapters.py` |
| Ambiguous paid timeout | No request retry; reservation remains charged | `tests/unit/test_model_planner.py` |
| Over-budget response | Stop without further provider/tool calls | `tests/unit/test_model_planner.py` |
| Fake model completion | Never `task_completed=True` | `tests/integration/test_agent_execution.py` |
| Raw source stored in ledger | Store hashes and event metadata, not excerpts | `tests/integration/test_agent_execution.py` |
| Wrong project | Require known manifest and registered project ID | `tests/contract/test_mcp_server_contract.py` |

## Recovery boundary

Local events are append-only. A run failure records its error class and keeps previously accounted provider/tool receipts. Runs with an existing ID are refused rather than silently retried. This prevents pretending a request definitely failed when a write may already have landed. However, **checkpointed resumable multi-agent workflows and exactly-once semantics are not implemented**; they require a durable dispatcher, idempotent adapters and reconciliation.

## Execute tests

```bash
python -m pytest -q tests/unit tests/integration tests/security tests/contract tests/planning
python scripts/generate_api_docs.py
python scripts/build_site.py
python scripts/verify_docs.py
```

Read [Research](research.md) before interpreting test fixtures as scientific benchmark improvements. Unit-test coverage is not evidence of model quality.

##  code evidence

[The coding agent guide](coding-agents.md) and [sandbox threat model](sandboxing.md) document the new patch pipeline, Docker consent gates, forged receipt tests, recovery records, and controlled artifact export. The original checkout stays untouched and every run returns `task_completed: false`.

##  host-authored acceptance

A `TaskContract` pins the exact operator-selected test argv, approved file scope and literal post-change assertions. The coding service verifies the contract against actual host-recorded changes and test results. Mocked test receipts **cannot** produce `contract_verified=true`. A `verified` repair journal state means only that the declared contract passed with accepted host-side isolation evidence; no general semantic completion is claimed.

Repair attempts have a persistent journal and immutable idempotency fingerprint. The journal rejects concurrent or stale duplicate attempts; it reconstructs only fully recorded evidence after a crash. If it cannot establish whether a side effect landed, it blocks for human reconciliation instead of retrying. Budget reservations are made *before* potentially paid calls. Read [Repair loops](repair-loops.md).
