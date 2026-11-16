# Durable repair loops and acceptance contracts

> **Experimental v0.5:** The bounded repair coordinator is implemented and mock-tested. Real container execution and paid provider behavior still need connected integration tests. A successful test result is **not** a declaration that an arbitrary software task has been fully completed.

DecisionBound's repair system converts a coding request into **a fixed, operator-approved acceptance contract**, then executes up to five fresh proposals in separate disposable Git worktrees. The host—not the patch proposer—owns changed-file rules, regression test commands, patch approval, budget accounting, and completion evidence.

## Execution diagram

```text
Approved task + exact contract + idempotency key
                    │
       Validate project, Git HEAD and scope
                    │
         Transactionally reserve attempt
        (run ID + worst-case paid spend)
                    │
  Propose full patch against original source
                    │
      Validate patch and isolate worktree
                    │
         Run operator-approved tests
                    │
   Check unchanged diff + required predicates
                    │
          Record hash-bound receipts
                    │
        ┌───────────┴────────────┐
        │                        │
    Contract passes       Tests/contract fail
    real Docker            (safe retryable)
        │                        │
   `verified`          Bounded failure metadata
        │                        │
   Review patch          More attempts allowed?
                                 │
                          Reserve next attempt
```

**Important:** `verified` only means *the declared contract passed with host-validated Docker receipts*. It does not mean comprehensive semantic completion. `task_completed` remains `false` in coding outcomes, and no patch is merged or pushed.

## Author an acceptance contract

Save the following as `contract.json` outside the untrusted code output. The operator—not the coding model—must review and approve it.

```json
{
  "schema_version": "1.0",
  "contract_id": "fix-value-defect-v1",
  "allowed_paths": ["app.py", "tests/test_app.py"],
  "required_paths": ["app.py"],
  "test_commands": [
    ["python", "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_app.py"]
  ],
  "assertions": [
    {"path": "app.py", "contains": "VALUE = 2", "excludes": "VALUE = 999"}
  ]
}
```

Every path is checked against the immutable patch policy. A contract cannot authorize `.env`, credentials, CI workflows, Git internals, or agent instruction files. The source checkout must be a clean Git root. A contract is SHA-256 pinned to its repair session, along with the starting Git commit, project ID, selected source paths, retry ceiling, and approved model-spend limit.

The contract's text assertions are **literal bounded substring conditions**, not arbitrary Python code or user-generated shell expressions. They complement, rather than replace, genuine regression tests.

## Offline demonstration — no paid model or Docker

```bash
python examples/mock_repair_agent.py
```

The example intentionally fails one scripted check and returns a passing mock receipt on the second attempt. Its terminal status is **`needs_real_tests`**, not verified or complete. The original checkout stays unchanged.

You can replay with the actual CLI using the same contract and your own fixed patch:

```bash
dbound code repair-mock 'Fix VALUE' \
  --path /path/to/clean/repo \
  --contract /path/to/contract.json \
  --key defect-1234-attempts \
  --patch-file /path/to/first.patch \
  --patch-file /path/to/second.patch \
  --test-exit 1 --test-exit 0 \
  --approve-writes

dbound code repair-status repair_<session-id>
```

A second call with the same key and the same request returns the existing terminal session, without generating another patch. Reusing that key for a different contract, Git base, task, or budget fails.

## Paid repair with external provider and isolated tests

Real execution is **off by default**. Install `.[test]` plus your provider runtime/keys, pre-pull a reviewed Docker image, and enable three independent permissions:

```bash
export OPENAI_API_KEY='...'

dbound code repair-provider 'Fix VALUE' \
  --path /path/to/clean/repo \
  --contract /path/to/contract.json \
  --key approved-defect-1234 \
  --provider openai --model YOUR_SUPPORTED_MODEL \
  --source-target app.py --max-attempts 2 \
  --input-price YOUR_INPUT_USD_PER_MILLION \
  --output-price YOUR_OUTPUT_USD_PER_MILLION \
  --max-cost 0.25 --image YOUR_PREPULLED_IMAGE \
  --allow-remote --allow-docker --approve-writes
```

`--max-cost` is the **total approved estimated/recorded model spend**, not a promise about a provider's final invoice. Uncertain calls keep a pessimistic budget reservation. Each call is preflighted by `ModelPatchProposer` before contacting the provider. Test commands come only from the contract; generated tool calls cannot change them.

## Crash recovery and replay protection

A repair session has an immutable fingerprint and a monotonic attempt journal. Each attempt receives a unique `code_...` ID and **pre-reserves its maximum possible model spend** before any paid request. Committed attempts reconcile charges against actual reported tokens; a crash before accounting keeps the reservation charged.

| Failure point | Reconnect behavior |
|---|---|
| Before reservation | Safe to start the first attempt normally |
| Reserved but provider call unknown | **Stop**: no automatic retry; operator must reconcile |
| Patch applied but tests unfinished | **Stop**: worktree and test side effects may be ambiguous |
| Fully recorded and worktree cleaned, journal not updated | Reconstruct only if the artifact hash, test/verification receipts and cleanup evidence agree |
| A completed failed test, with clean diff | A new attempt may use a bounded failure category |
| Invalid patch / forged receipt / modified extra file | **Block**, preserve evidence; never automatically retry |
| Reached attempt, time or USD budget | **Exhaust**, no further model call |

`--resume` is explicit. It may recover a fully recorded attempt or continue from an already completed failed attempt, but cannot replay an uncertain write. `dbound code pending` and `dbound code cleanup --approve-cleanup` remain available for operator-led worktree cleanup.

## Repair feedback and context costs

The proposer receives a **minimal diagnostic capsule**: failure kind (`failed_test` / `failed_contract`), failed test index and hash of the previous patch. It receives neither raw stdout/stderr nor complete prior agent transcripts, and every attempt creates a **full patch against the original selected source**. This prevents unbounded retry-context growth and unapproved changes to test commands.

The model factory uses the existing cost-aware JSON transport. A custom factory subclasses `RepairProposerFactory`; paid factories must reserve their whole remaining budget, while `ScriptedRepairFactory` declares itself offline. A custom factory runs in trusted host Python and does **not** gain additional write authority: all its patches still pass through `PatchValidator`.

## Security caveats

A Git worktree isolates edits, **not process execution**. Use the `DockerTestRunner` for real untrusted tests; it requires a trusted daemon and reviewed image. CLI native adapter processes are not a replacement for sandboxing. This alpha is single-user SQLite-based and does not implement distributed worker leases, unattended remediation, secure multi-tenancy, or a production container certification.

See [Coding agents](coding-agents.md), [Native harness adapters](native-adapters.md), [Sandboxing](sandboxing.md), [Verification](verification.md) and [API reference](api-reference.md).


## Test-first acceptance for supervised bug fixes ()

`SupervisedCodingRequest(require_red_test=True)` requires that the first
operator-authored `TaskContract.test_commands` command **fails on the original
Git commit** and later **passes on the patched worktree**, both under the
real `DockerTestRunner`. Pre-patch evidence is recorded with a hash, sanitized
exit status and exact command fingerprint. Mock test results cannot satisfy
this gate. A retry in a fresh worktree repeats the red check deliberately;
unknown in-flight writes remain quarantined rather than replayed.

The `require_red_test` setting is included in the repair idempotency digest.
For existing  requests without the setting, the original digest
remains compatible. The supervised workflow fingerprint separately includes
contract identity, runner type/image and scripted patch hashes to reject
incompatible resubmission.

See [Supervised coding](supervised-coding.md) for a CLI/SDK example.
