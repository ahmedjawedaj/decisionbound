# Coding-agent execution

> **Alpha 0.4 · safety status:** Git patch generation/application is available with explicit consent in detached, disposable worktrees. Real tests require a separately authorized Docker backend. There is **no automatic merge or semantic task-completion claim**. Mock tests do not certify correctness.

 adds [bounded repair loops and task-specific acceptance contracts](repair-loops.md), plus [proposal-only Claude/Codex CLI adapters](native-adapters.md). The core design extends [agent execution](agent-execution.md) with one bounded **proposal → validate → isolate → test → verify → export** pipeline. This is a coding-agent *vertical slice*, not yet a full Claude Code/Codex equivalent, multi-agent dispatcher, repair swarm or production-certified sandbox.

## First run: inspect without writing

From a clean Git repository initialized with DecisionBound:

```bash
python -m pip install -e '.[test,web,docs]'
cd /path/to/my-git-repository
dbound init --technologies python
# Make sure git status is clean (an untracked .decisionbound/ manifest is permitted).
# Obtain or create a small text-only Git diff at /tmp/change.patch.
dbound code inspect --path . --patch-file /tmp/change.patch
```

The inspect command reports the exact paths, added/deleted line counts and a SHA-256 hash. It cannot modify source files or create a worktree.

## Offline end-to-end demo (no Docker or model key)

```bash
python examples/mock_patch_agent.py
```

Or use your own candidate patch:

```bash
dbound code mock-run 'Fix the value' --path . \
  --patch-file /tmp/change.patch --approve-writes
```

Mock testing **really creates and modifies a detached Git worktree**, then removes it, while keeping your original checkout untouched. The fake test receipt is labeled `kind: mock`. Both `acceptance_verified` and `task_completed` remain **false**. The mock is not a security sandbox for executing untrusted code, because it does not execute any.

## Real container-based test execution

Prerequisites: local trusted Docker daemon, a non-root caller, a pre-pulled reviewed image (ideally pinned by digest), clean Git root, initialized project, reviewed patch, and explicit authorization. A root caller receives `PermissionError` before the daemon is contacted. Platforms without POSIX user IDs use container UID/GID `1000:1000`.

```bash
# Use a reviewed image already available locally; for illustration only:
docker pull python:3.12-slim

dbound code apply 'Fix the value' \
  --path . --patch-file /tmp/change.patch \
  --approve-writes --allow-docker --image python:3.12-slim \
  --test-cmd 'python -c "import app; assert app.VALUE == 2"'
```

The command inside `--test-cmd` is parsed as an argument vector and executed **inside Docker only**, never through a shell on the host. Multiple `--test-cmd` options execute sequentially and stop on the first failure. Use a command that avoids writing to tracked or ignored workspace files. For pytest, use `python -m pytest -p no:cacheprovider -q` to prevent `.pytest_cache` artifacts, and disable other test cache plugins when needed.

The container runs with `--network none`, `--pull never`, read-only container filesystem, dropped capabilities, no-new-privileges, explicit non-root UID/GID, process/memory/CPU limits, a temporary `/tmp`, timeout enforcement, and **only the disposable worktree mounted writable**. We do not mount Git credentials, the Docker socket, SSH secrets or the user's home directory. The Docker daemon itself remains a privileged trust boundary and is not certified by these tests. The host rejects nonlocal `DOCKER_HOST` configurations, does not pass on `DOCKER_CONTEXT` or `DOCKER_CONFIG`, and pins the `docker` context to `default`, so a context saved with `docker context use` cannot move the tests to another daemon. Each test receipt records the image reference as `image`, the `sha256:` digest it pins as `image_digest` (empty for a mutable tag such as `python:3.12`) and the daemon as `daemon_endpoint`. Set `DBOUND_REQUIRE_IMAGE_DIGEST=1` to refuse any image that is not pinned by digest. Windows Docker/WSL path behavior needs end-user validation.

### Model-generated patch, user-selected files only

An explicitly opted-in provider can propose **one** text patch, limited to caller-selected files and bounded, approved context:

```bash
export OPENAI_API_KEY='...'    # use your own protected environment

dbound code propose 'Fix the validation bug' \
  --path . --target app.py \
  --provider openai --model YOUR_MODEL \
  --allow-remote --input-price YOUR_INPUT_USD_PER_M \
  --output-price YOUR_OUTPUT_USD_PER_M --max-cost 0.25 \
  --approve-writes --allow-docker \
  --image python:3.12-slim \
  --test-cmd 'python -c "import app; assert app.VALUE == 2"'
```

The provider receives only selected UTF-8 source files (8 files max, 16 KiB each and 48 KiB total) plus relevant approved decision context. It cannot enumerate arbitrary files through this method. Provider credentials come from environment variables and are never stored in profiles, context or the run ledger. Costs are estimated before a call; ambiguous failures are not retried. Live-model compatibility and actual metering must be tested with approved credentials.

## Outputs and review

Every successful patch-application attempt produces a `CodingOutcome` receipt containing: run ID, pinned Git base SHA, patch SHA-256, canonical diff SHA-256, changed paths, a private 0600 patch-artifact path, isolated test receipts, and an independent verification decision.

```bash
dbound trace code_<run-id>
# For an unverified or mock run you must explicitly acknowledge limitations:
dbound code export code_<run-id> --output /tmp/review.patch \
  --acknowledge-unverified
```

`code export` verifies artifact bytes against host-recorded hashes and refuses overwrites. Even a verified patch is **never** merged, committed, pushed, deployed or applied to your source checkout automatically. Review the exported patch and use your own Git workflow to accept it. `task_completed` remains `false` because a passing test suite alone is not a semantic proof of the requested change.

## Recovery after interruption

A crash can leave an abandoned detached worktree; the service does **not** automatically re-run side effects:

```bash
dbound code pending
dbound code cleanup code_<run-id> --approve-cleanup
```

Cleanup verifies the original repository and recorded worktree path match a managed run. If metadata or Git state conflicts, it fails closed for operator inspection. Patches and event receipts survive worktree cleanup under the local state directory.

## Security limits and prohibited operations

The host refuses symlinked targets/ancestors, absolute and traversal paths, hidden project control metadata, CI workflows, `.env`, `.envrc` and other environment or credential-like files, agent instruction files (`AGENTS.md`, `CLAUDE.md`) in any directory, Git submodules/symlinks/executables, binary patches, renames/copies/deletions, missing or duplicate hunk headers, mismatched changed paths and changes introduced during testing. It does **not** provide adversarial runtime isolation on the host; you must use the configured container runner for real untrusted tests. Full threat modeling, remote multi-tenant authorization, Docker escape hardening and OS-specific sandbox assurance remain separate work.

## Extending the coding system

Subclass `PatchProposer` to integrate another model or developer-review workflow. Subclass `IsolatedTestRunner` for an alternative sandbox, but such runners are **not automatically trusted to certify outcomes**: acceptance currently requires the built-in restricted `DockerTestRunner` plus independent host receipts. `CodeReceiptVerifier` adds evidence validation; custom verifiers can only restrict acceptance, never bypass host verification. For an alternative backend to become trusted, its provenance/conformance contract and host admission process must be explicitly extended and tested.

See [Sandboxing & threat model](sandboxing.md), [Verification](verification.md), [API reference](api-reference.md) and [GitHub Pages deployment](pages.md).
