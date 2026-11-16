# Supervised coding: prepare → implement → audit

> ** · alpha 0.7.0a1.** This feature is a bounded, operator-authorized coding workflow. It does not automatically merge changes, and even an accepted code contract **does not** establish broad task-level semantic success.

## What changed

The shared workflow engine now supports a dedicated `review` kind. It is deliberately different from an agent's natural-language assertion that code looks good:

1. `prepare` selects task-relevant project evidence with a fixed context limit.
2. `code` uses an operator-approved `TaskContract` to propose a patch, create a detached Git worktree, run exact test commands, and record host-owned receipts. Repair attempts and paid calls are capped by the existing  journal.
3. `review` runs only after the code stage is **verified** (not `mock_passed` or `needs_real_tests`). It independently reconstructs the repair session using the workflow-bound idempotency key, checks real Docker test receipts, validates the patch artifact's bytes and permitted scope, and confirms that the original source checkout still points to the recorded base commit.

The workflow's final `verified_stages` label means the three stages passed their stated checks. The public result retains `task_completed: false` and `patch_auto_merged: false`. Reviewer results are proof of the recorded tests and files, **not** proof of correctness beyond the approved acceptance contract.

## Run a safe, offline rehearsal

The mock mode creates a real isolated worktree and applies an operator-provided patch but **does not execute tests**. It is useful for checking command ergonomics, filesystem isolation, journaling, and refusal to certify simulated success.

Create a checked-in Git repository containing `app.py` with `VALUE = 1` and initialize it with `dbound init --path .`. Prepare a trusted, local contract:

```json
{
  "schema_version": "1.0",
  "contract_id": "value_regression",
  "allowed_paths": ["app.py"],
  "required_paths": ["app.py"],
  "test_commands": [["python", "-c", "import app; assert app.VALUE == 2"]],
  "assertions": [{"path": "app.py", "contains": "VALUE = 2", "excludes": ""}]
}
```

Prepare a valid unified patch in `change.patch`:

```diff
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
```

Run the mock rehearsal:

```bash
dbound workflow supervised-code "Change value to two" \
  --contract contract.json --patch-file change.patch \
  --source-target app.py --path . --key value_review_01 \
  --mode mock --approve-writes
```

Expected task states: `plan: verified`, `implement: needs_real_tests`, `audit: blocked`. The overall workflow is `blocked` because an unverified code stage cannot unlock the reviewer. The original checkout remains unchanged.

## Connected Docker verification

Only on a trusted machine with a working Docker daemon and a pre-pulled image:

```bash
docker pull python:3.12-slim

dbound workflow supervised-code "Change value to two" \
  --contract contract.json --patch-file change.patch \
  --source-target app.py --path . --key value_real_01 \
  --mode docker --docker-image python:3.12-slim \
  --allow-docker --approve-writes --require-red-test
```

`--require-red-test` adds a **test-first gate** for bug fixes: the *first command*
from the operator-authored acceptance contract must fail on the original
commit, then pass after the patch. Both receipts must come from the real Docker
runner, must not time out, and must refer to the **same command**. This does not
prove that the original failure was caused by the targeted bug rather than
another environment issue; keep regression commands specific and review their
behavior on the connected machine. Offline `mock` mode cannot pass this gate.

The immutable workflow fingerprint binds the contract, source targets,
model budget/attempt limits, runner type and image, optional test-first mode,
and hashes of scripted patches. A reused idempotency key with incompatible
configuration fails closed. A custom provider factory must use a new key when
its external model or prompt configuration changes.

The built-in Docker runner uses a network-disabled, privilege-dropped, non-root, resource-bounded container with a read-only container filesystem and a single writable isolated Git worktree bind mount. Docker still requires a trusted local daemon, reviewed image provenance and proper host isolation; this project has **not** independently certified container escape resistance. For release, pin images by digest and record the reviewed digest in the test manifest.

Expected result *if real integration checks pass*: all three stage states `verified`, workflow state `verified_stages`, and `task_completed: false`. Docker is unavailable in the local authoring sandbox, so this is a connected-environment acceptance criterion, not a claimed local result.

## Reusable Python SDK

```python
from pathlib import Path
from decisionbound.code_acceptance import TaskContract
from decisionbound.code_repair import RepairLimits, ScriptedRepairFactory
from decisionbound.code_sandbox import DockerTestRunner
from decisionbound.settings import repository, state_dir
from decisionbound.supervised_coding import SupervisedCodingRequest, SupervisedCodingOrchestrator

contract = TaskContract.from_dict(...)  # approved, immutable versioned contract
patch = Path('change.patch').read_text(encoding='utf-8')
request = SupervisedCodingRequest(
    'Change value to two', contract, ('app.py',),
    limits=RepairLimits(max_attempts=1, max_model_cost_usd=0),
)
result = SupervisedCodingOrchestrator(
    repository=repository(), state_root=state_dir(),
    runner=DockerTestRunner('python:3.12-slim'),
    proposer_factory=ScriptedRepairFactory((patch,)),
).execute(project_path=Path('.'), request=request,
          idempotency_key='approved_value_change', approved_writes=True)
```

Review evidence can be re-attested without rerunning code, even after the original workflow finished:

```bash
dbound workflow attest wf_EXAMPLE
```

Or call `GET /api/workflows/{workflow_id}/attestation` on the authenticated local API, or invoke the project-scoped read-only MCP tool `get_workflow_attestation`. A changed artifact or Git base causes `review_attestation: stale`. An unfinished review reports `not_verified`. This is a point-in-time integrity check and never marks `task_completed: true`.

`CodeReviewPolicy` can be subclassed to **reject** additional findings. It cannot turn an incomplete, stale, or simulated code receipt into a successful review. The canonical `IndependentTaskVerifier` recomputes the digest outside the review worker.

## Failure/recovery matrix

| Scenario | Behavior |
|---|---|
| Missing `--approve-writes` | No workflow is created and no patch is applied |
| Model claims tests passed but uses simulated runner | Code stage `needs_real_tests`; reviewer blocked |
| Test fails or contract rejects patch | Bounded repair attempts, then failure/exhaustion |
| `--require-red-test` with a simulated runner | Blocked before patch application; no mock-derived red proof |
| Reused key after contract, patch, runner or test-first change | Conflict, no replay or silent upgrade |
| Patch touches `.env`, AGENTS.md or unapproved path | Rejected by host patch validator before writing |
| Code process interrupted with ambiguous write | Lease quarantined; no automatic replay |
| Patch artifact modified after a code run | Reviewer fails byte-level SHA-256 check |
| Git HEAD changes or original checkout becomes dirty | Reviewer refuses stale base snapshot |
| Fake `reviewed` receipt submitted | Host recomputation rejects it |
| All three host stages verified | Workflow reports verified **stages**, never broad task completion |

## Integrating in CI

The connected `docker-code-sandbox` CI job runs `tests/integration/test_supervised_docker_real_optional.py` using a real container, in addition to the existing single coding-run Docker test. Offline tests simulate host ledgers solely to check provenance logic; they do **not** certify Docker execution.

See [Sandboxing](sandboxing.md), [Repair loops](repair-loops.md), [Orchestration](orchestration.md), [Security](security.md) and the [generated API reference](api-reference.md).
