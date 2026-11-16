# Durable multi-agent orchestration

> ** · alpha 0.7.0a1.** The local supervisor is implemented and tested offline. It is **not** a distributed job cluster, a hard-kill process sandbox or a replacement for native Claude/Codex workers. Real provider, Docker and MCP wire tests are separate release gates.

## What problem does it solve?

DecisionBound's workflow engine coordinates bounded jobs without trusting an agent's self-reported success. It tracks immutable task contracts, independent verification, concurrency limits, role-specific workers, dependency gates, pre-call budget reservations, leases, cancellation and restart reconciliation. Every transition is stored in SQLite in the same transaction as its state change.

**Result labels:** `verified_stages` means every requested *stage* passed its own host-side verification. It **does not** mean the user's overall request was completed. `needs_real_tests` means one or more stages were mock-tested or their actual sandbox checks are missing. `blocked` requires an operator to inspect failures. `running` can also mean the graph is awaiting an unregistered worker. `cancelled` revokes claim ownership, but does not kill an already running subprocess.

## Architecture

```text
CLI / Local API / MCP (read-only inspection)
        |
        v
Immutable WorkflowSpec -- project identity check
        |
        v
WorkflowJournal (SQLite BEGIN IMMEDIATE)
  +--> ready DAG tasks / dependency gate
  +--> role-specific route selection
  +--> budget reservation / lease generation
        |
        v
WorkflowSupervisor (bounded local ThreadPoolExecutor)
  +--> PreparationWorker  -> evidence context hash
  +--> ModelInspectionWorker -> bounded read-only tool loop + trace
  +--> ScriptedWorker     -> mock_passed only
  +--> CodingRepairWorker ->  worktree/contract/test journal
        |
        v
IndependentTaskVerifier + host journal gate
        |
        v
CAS completion / metadata-only events / verified handoff fingerprint
```

The worker-facing `TaskAssignment` **does not include** the secret lease token, owner generation or any provider credentials. It can include `TaskHandoff` entries for verified prerequisites: task identity, kind and immutable receipt fingerprint. It deliberately does **not** copy an entire preceding model context, hidden reasoning, raw tool logs or repository files into every worker's prompt. Downstream agents must retrieve only the evidence they need from the authorized source.

## Fresh user experience (no account or model key required)

Initialize the repository once:

```bash
cd my-repository
dbound init --technologies python
```

Check and submit the example graph:

```bash
dbound workflow validate --file examples/workflow/repository-review.yaml
dbound workflow submit --file examples/workflow/repository-review.yaml --path .
# Copy the workflow_id from the JSON receipt
dbound workflow run wf_EXAMPLE
dbound workflow status wf_EXAMPLE
dbound workflow events wf_EXAMPLE
```

CLI auto-generated idempotency keys are derived from the resolved workspace and canonical graph. Submitting an identical graph again returns the same workflow; a changed graph creates a different workflow. An explicit idempotency key can be set by the caller. Submitting a key that belongs to a different request fails without replacing the previous graph.

Use `dbound workflow reconcile wf_EXAMPLE` after process interruption or `dbound workflow cancel wf_EXAMPLE` to revoke pending claims. Neither command executes arbitrary code or makes remote calls.

## Workflow manifest

```yaml
schema_version: 1
max_workers: 2
max_total_cost_usd: 0
tasks:
  - task_id: inspect
    kind: prepare
    role: researcher
    description: Locate relevant architectural constraints
    max_attempts: 2
    max_context_tokens: 1200
  - task_id: review
    kind: inspect
    role: reviewer
    description: Use read-only source inspection tools
    depends_on: [inspect]
    max_cost_usd: 0.05
```

The default CLI activates only offline `prepare` and explicitly simulated `mock` workers. A model-driven `inspect` task requires an SDK-injected `ModelInspectionWorker` with a provider transport factory and explicit pricing. Its child run's host tool receipts, project identity, task hash, and workflow correlation fingerprint are checked before the inspection stage can be verified. A `code` task remains pending until the application injects a consented `CodingRepairWorker`, with its `TaskContract`, `PatchProposer`, isolated test runner and limits. It cannot be enabled by arbitrary instructions in the task manifest.

Each task has a maximum 3,000-character description, up to 16 prerequisites, and four execution kinds (`prepare`, `inspect`, `mock`, `code`, `review`). A workflow allows 1–50 tasks, 1–8 local concurrent workers and an explicit total budget no higher than $100. These limits are application-level guardrails, not proof of safe unattended operation.

## Worker extension contract

Subclass `WorkflowWorker` and implement `execute(assignment: TaskAssignment) -> WorkerReceipt`. Never execute shell commands from model output or hold a global token in the agent context. For specific roles, register workers by `prepare:reviewer`, `prepare:researcher`, or an unqualified kind as fallback. Each worker is subject to the host verifier and lease CAS boundary.

```python
from decisionbound.workflows import WorkflowWorker, WorkerReceipt, PreparationWorker

class ReviewedPreparation(WorkflowWorker):
    def __init__(self, store):
        self.inner = PreparationWorker(store)

    def execute(self, assignment):
        # Narrow, specialized role. No owner_token in assignment.
        assert assignment.task.role == "reviewer"
        return self.inner.execute(assignment)
```

Application integration:

```python
from decisionbound.workflows import WorkflowJournal, WorkflowSupervisor
from decisionbound.settings import repository

store = repository()
journal = WorkflowJournal(store)
result = WorkflowSupervisor(journal, workers={
    "prepare:reviewer": ReviewedPreparation(store),
    "prepare": PreparationWorker(store),
}).run("wf_...", max_wall_seconds=120)
```

For code execution, use `CodingRepairWorker` with an explicitly approved task mapping. The `ModelInspectionWorker` reuses 's strict model/tool JSON action contract and places an independent trace under the workflow receipt. Model patch proposals can be routed through the existing  `RepairProposerFactory`, but the worker cannot grant itself write authority or convert mocked test results to verified code acceptance.

## Fault model and recovery rules

| Event | What happens | What the operator must know |
|---|---|---|
| Two local dispatchers race for the same task | SQLite serializes claims with `BEGIN IMMEDIATE` | Exactly one lease owner is recorded |
| Read-only, zero-paid-cost task lease expires | May retry if under attempt budget | Late completion is rejected by generation/token check |
| Paid task becomes ambiguous | Task fails closed; spending reservation remains charged | Reconcile actual provider/billing state manually |
| Code task lease expires after potential write | `blocked` / `unknown_write_outcome` | Inspect the  repair journal and worktree before resuming |
| Mock worker claims success | `mock_passed`, not `verified` | Dependent real tasks cannot proceed |
| Worker sends a fake observation | Canonical host verifier rejects it | Workers cannot certify their own outputs |
| Worker reports spending above reserved cost | Task quarantined; no retry | Costs must be resolved independently |
| User cancels workflow | Pending/leased claims are revoked | Already running local processes **are not** forcibly terminated |
| Missing code worker | Workflow remains `running` with the task pending | Configure an approved worker externally |

**Limits:** leases are cooperative. A local Python thread cannot be forcibly killed without process isolation. `max_wall_seconds` limits dispatch decisions between completed worker calls; it does not impose a hard timeout on a blocked provider or subprocess. Real Docker isolation, cgroups, process cancellation, distributed PostgreSQL leases and automated paid-provider reconciliation remain open work. Task descriptions are persisted in a private local SQLite table so crashes can be resumed; event logs and public API status responses intentionally omit raw descriptions and credentials.

## Testing and research

```bash
python -m pytest -q tests/integration/test_workflow_sprint06.py
python -m pytest -q tests/integration/test_workflow_cli_web_sprint06.py
python -m pytest -q tests/contract/test_mcp_server_contract.py
python scripts/generate_api_docs.py
python scripts/build_site.py
python scripts/verify_docs.py
```

Performance and cost claims are not established by mock throughput. A follow-up experimental phase must compare single-agent and multi-agent performance on the *same* tasks, models, budgets and verification contracts, reporting P50/P95 latency, cost per independently verified success, handoff error rates, and negative results. No thesis claim should be inferred from a deterministic synthetic graph.


## independently reviewed code stage

A `review` task has exactly one `code` predecessor and must declare `role: reviewer`.
It runs only after the preceding coding task passed real, host-controlled sandbox
checks. The review worker reconstructs the repair journal, patch checksum,
accepted contract and Git base; its claim is independently rebuilt by the
host verifier. This does not replace a human or semantic code review.

The supported convenience SDK/CLI is documented in [Supervised coding](supervised-coding.md).
A separate `dbound workflow attest WORKFLOW_ID` command reports whether the
review evidence is still current; it never replays a coding worker.
