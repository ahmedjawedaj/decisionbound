# CLI command reference

Commands target a local workspace and shared user registry. JSON output is stable enough for alpha smoke testing, but should not yet be treated as a frozen versioned API.

| Command | Purpose | Side effects |
| --- | --- | --- |
| `dbound init` | Register project and create manifest | Local files and DB |
| `dbound status` | Inspect project/profile | None |
| `dbound profile list` | Discover selectable presets | None |
| `dbound profile use NAME` | Change active profile | Local DB |
| `dbound profile create NAME --from PRESET` | Create custom end-user profile | Local DB |
| `dbound profile export NAME --output FILE` | Export safe YAML | Explicit file creation |
| `dbound profile import FILE` | Import reviewed YAML | Local DB |
| `dbound decision discover ADR` | Extract unapproved ADR decision | Local DB |
| `dbound decision approve ID --revision N` | Approve unchanged evidence | Local DB |
| `dbound decision list` | Inspect current-project decisions | None |
| `dbound transfer` | Preview compatibility and abstentions | None |
| `dbound prepare TASK --tokens N` | Compile bounded task capsule | None |
| `dbound run TASK` | Execute offline preparation and audit | Local events |
| `dbound trace RUN_ID` | Replay durable events | None |
| `dbound skills` | Discover SKILL.md metadata | None |
| `dbound agent demo TASK` | Deterministic model-shaped read-only tool loop | Local trace events |
| `dbound agent run TASK --model ... --allow-remote` | Real opt-in read-only JSON model agent | Authorized provider requests and local traces |
| `dbound mcp serve --path PATH` | Official-SDK MCP v2 read-only stdio server | Local MCP subprocess |
| `dbound serve` | Start optional loopback API/UI (incl. mock agent demo) | Local token and listening socket |

`dbound` without arguments shows help. **Not shipped:** autonomous code editing, sandboxed shell execution, cloud credential managers, remote distributed workers, general MCP client/plugin hosting and publishable benchmark claims. The model-driven read-only `agent run` is strictly opt-in and its result is never labeled task-complete. See [Agent execution](agent-execution.md).

## New: code-review and isolated patch commands (0.4 alpha)

```bash
dbound code inspect --path . --patch-file /tmp/change.patch
dbound code mock-run 'Fix the bug' --path . --patch-file /tmp/change.patch --approve-writes
dbound code apply 'Fix the bug' --path . --patch-file /tmp/change.patch \
  --approve-writes --allow-docker --image python:3.12-slim \
  --test-cmd 'python -c "import app; assert app.VALUE == 2"'
dbound code pending
dbound code export code_<run-id> --output /tmp/review.patch --acknowledge-unverified
dbound code cleanup code_<run-id> --approve-cleanup
```

For provider-backed code proposals, use `dbound code propose` with explicit `--target` files, model, provider pricing, remote consent, write consent and Docker consent. These are deliberately not implicit in an active profile. Read the [coding-agent guide](coding-agents.md) for prerequisites, output semantics, and unverified integration limits.

##  repair commands (alpha)

| Command | Purpose | Execution boundary |
| --- | --- | --- |
| `dbound code repair-mock TASK --contract FILE --key KEY --patch-file FILE ... --approve-writes` | Bounded deterministic multi-attempt repair | Real isolated Git patches, mocked test receipts |
| `dbound code repair-provider TASK --contract FILE --key KEY --model MODEL --max-cost USD --allow-remote --allow-docker --approve-writes ...` | Optional paid model repair | Pre-reserved spending; requires approved Docker runner and provider |
| `dbound code repair-status SESSION_ID` | Inspect local durable state | Read-only metadata and SHA hashes |
| `dbound code native-propose TASK --harness claude\|codex ... --allow-native --allow-docker --approve-writes` | One-shot harness CLI proposal | Optional external process; cannot change host policy |

Commands never auto-commit, auto-merge, or push. Running real-provider or native clients needs explicit local authorization. Full details and contract format: [Repair loops](repair-loops.md) and [Native adapters](native-adapters.md).

##  local workflow commands

```bash
dbound workflow validate --file examples/workflow/repository-review.yaml
dbound workflow submit --file examples/workflow/repository-review.yaml --path .
dbound workflow run wf_EXAMPLE  # offline preparation + synthetic workers only
dbound workflow status wf_EXAMPLE
dbound workflow events wf_EXAMPLE
dbound workflow reconcile wf_EXAMPLE
dbound workflow cancel wf_EXAMPLE
dbound workflow reap-orphans   # kill worker trees left by a dead coordinator
```

`reap-orphans` takes the coordinator lease while it runs, signals only processes whose recorded start token still matches, and prints counts. It refuses to run while a coordinator is active. See [Worker containment](worker-containment.md).

Every CLI command uses the same SQLite state as the authenticated local API and the read-only project-bound MCP tools. The manifest controls graph topology and budgets; it **does not** authorize paid model calls, code writes or external credentials. To register a model-driven inspection worker or an approved code repair worker, use the Python SDK and explicit dependency injection. See [Multi-agent orchestration](orchestration.md).


## End-user onboarding ()

```bash
dbound setup --profile local-safe --path . --initialize
dbound doctor --path . --strict
dbound setup --interactive
```

`doctor` emits stable JSON, prints no secret values, and exits with code 2 when `--strict` finds missing required prerequisites. Setup never invokes an LLM or approves a code write. More: [Setup & diagnostics](setup-diagnostics.md).

## Private state integrity and recovery

```bash
dbound state check --full
dbound state backup --output "$HOME/private-dbound-backups/state.db"
dbound state restore --backup "$HOME/private-dbound-backups/state.db" --destination "$HOME/private-dbound-backups/recovered.db"
```

These actions never contact an LLM or overwrite active SQLite state. Backups contain private metadata. See [database recovery](state-recovery.md) for operating safeguards and the full failure model.
