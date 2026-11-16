# Python SDK — dependency injection and deliberate overrides

Use the **public ports** in `decisionbound.ports` as the stable extension boundary. Prefer new adapter implementations over monkey-patching database internals.

## Construct the default local stack

```python
from pathlib import Path
from decisionbound import SQLiteRepository, ConservativeTransferPolicy, SelectiveContextCompiler
from decisionbound.services import DecisionService

store = SQLiteRepository(Path.home() / ".decisionbound" / "state.sqlite3")
service = DecisionService(store, ConservativeTransferPolicy(), SelectiveContextCompiler())
```

The service is dependency-injected: any conforming `DecisionRepository`, `TransferPolicy`, or `ContextCompiler` implementation can replace the built-in.

## Override transfer behavior safely

```python
from collections.abc import Iterable
from decisionbound import Decision, Project, TransferResult
from decisionbound.ports import TransferPolicy

class TeamVetoPolicy(TransferPolicy):
    """Team-specific decision gate layered on the conservative policy."""

    def __init__(self, blocked: set[str]):
        self.blocked = blocked

    def evaluate(self, decision: Decision, target: Project,
                 forbidden: Iterable[str] = ()) -> TransferResult:
        from decisionbound import ConservativeTransferPolicy
        # Preserve base authority checks, even in an override.
        return ConservativeTransferPolicy().evaluate(
            decision, target, set(forbidden) | self.blocked
        )
```

The source checkout includes an executable `examples/custom_policy.py`. The repository is public at <https://github.com/ahmedjawedaj/decisionbound> under Apache-2.0.

## Add a context compiler

Implement `ContextCompiler.compile(task, items, max_tokens)` and return an immutable `ContextCapsule`. Use `ContextBudgetExceeded` when protected requirements cannot fit. Never silently truncate security or authority constraints.

## Add a storage backend

Implement all methods of `DecisionRepository`, including atomic compare-and-swap status updates. A PostgreSQL adapter must provide its own locking, migrations, and test isolation; merely subclassing `SQLiteRepository` is **not** a correct distributed implementation.

## Add a worker and verifier

Implement `AgentWorker.run()` and `TaskVerifier.verify()` separately. A worker's reported success must not become evidence of objective completion. Restrict filesystem/network actions outside the model and test tool permissions independently.

## Plug in a model transport

Implement `JSONModelTransport.complete(messages, schema, max_output_tokens) -> ModelCompletion`. Its output is always revalidated through `parse_action` and the host tool registry; an implementation cannot bypass write restrictions. For local tests, prefer `ScriptedModelTransport`. Example:

```python
from decisionbound.model_planner import ScriptedModelTransport, Pricing
from decisionbound.execution import run_readonly_agent
from decisionbound.storage import SQLiteRepository

# Root must have been initialized and registered in the same SQLite store.
transport = ScriptedModelTransport([
    '{"kind":"finish","tool":null,"arguments_json":"{}","message":"No tool needed"}'
])
result = run_readonly_agent(
    project_path=project_root, store=store, task="Inspect a module",
    transport=transport, pricing=Pricing(0, 0),
)
assert result["task_completed"] is False
```

When using real providers, supply a `Pricing` and a `ModelLimits` object and configure the provider explicitly. Remote model credentials belong only in environment variables or a secret manager. Read the [agent execution security contract](agent-execution.md) before implementing a transport.

## Public API reference

[Generated API reference](api-reference.md) is extracted from source-level class and method docstrings during the site build. This helps detect missing documentation and outdated copied signatures.

## Coding agent SDK (0.4 alpha)

The supported override contracts are `PatchProposer`, `IsolatedTestRunner`, and `CodingVerifier` in `code_ports.py`. Callers should use `run_coding_task` rather than applying patches directly, because this function enforces write approval, registered project identity, a clean Git base, strict file scope, private artifact receipts, and independent verification.

The two built-in worker options are `PatchFileProposer` (deterministic local patch) and `ModelPatchProposer` (one structured, budgeted model-generated patch). `ScriptedTestRunner` is strictly for mock testing. Only the built-in `DockerTestRunner` can currently contribute to real isolated acceptance; another test-runner override does not automatically receive its trust level. See [Coding agents](coding-agents.md) for examples and [API reference](api-reference.md) for docstring-generated signatures.

##  extension contracts

- `TaskContract` in `code_acceptance.py`: operator-authored immutable test commands, permitted/required file scope, literal content predicates, and versioned SHA-256 fingerprint. A model's proposed contract does not become authoritative.
- `RepairProposerFactory` in `code_repair.py`: override `make(attempt, feedback, remaining_cost_usd)` for a fresh proposer. **Paid-capable by default** (`requires_budget_reservation=True`); override it to `False` only for a genuinely offline, deterministic implementation.
- `SQLiteRepairJournal`: the current reference local durable adapter. Clients may not manipulate its SQL tables to promote verification. `RepairState.to_dict()` is a bounded, source-free status view.
- `CodexCLIPatchProposer` and `ClaudeCLIPatchProposer`: optional external CLI shims, not full native agent SDK implementations. No custom adapter may gain patch write/test/merge authority.

Read [Repair loops](repair-loops.md) and [Native adapters](native-adapters.md) for contract and override examples. API signatures and docstrings are regenerated in [API reference](api-reference.md).

## End-to-end example

This script creates temporary state, discovers an ADR, approves its unchanged evidence as an explicit operator decision, and prepares a context capsule. It is executed by the test suite.

<!-- sdk-quickstart:begin -->
```python
from pathlib import Path
from tempfile import TemporaryDirectory

from decisionbound import SQLiteRepository
from decisionbound.identity import init_project
from decisionbound.services import DecisionService

with TemporaryDirectory() as directory:
    workspace = Path(directory) / "example-project"
    workspace.mkdir()
    store = SQLiteRepository(Path(directory) / "state" / "state.sqlite3")
    project = init_project(workspace, store, name="example-project",
                           technologies=("python",))

    (workspace / "architecture.md").write_text(
        "# Domain boundaries\n\n"
        "## Context\nKeep transport logic easy to test.\n\n"
        "## Decision\nKeep handlers thin and isolate domain rules.\n",
        encoding="utf-8",
    )

    service = DecisionService(store)
    candidate = service.discover_adr(project, workspace, "architecture.md")
    # This approval is an explicit application/operator decision.
    service.approve(workspace, candidate.decision_id, candidate.revision)
    capsule = service.prepare("Inspect domain boundaries", project,
                              max_tokens=1200)
    print(capsule.to_dict())
```
<!-- sdk-quickstart:end -->

Output from a real run (identifiers differ each time; `items` is a tuple, so Python prints it in parentheses):

```text
{'task': 'Inspect domain boundaries', 'items': ({'key': 'dec_...', 'text': 'Keep handlers thin and isolate domain rules.', 'source': 'architecture.md#sha256=bc4a61de...', 'protected': False, 'priority': 10},), 'omitted_keys': (), 'estimated_tokens': 18, 'max_tokens': 1200}
```
