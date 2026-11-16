# Claude and Codex — proposal-only CLI adapters

> These are **optional, mock-tested CLI integration boundaries**, not full managed Codex/Claude SDK sessions. Live authentication, CLI-version compatibility, actual usage charges and real returned proposal formats must still be checked on a connected host. They cannot launch arbitrary coding tools through DecisionBound's host permissions.

DecisionBound provides two `PatchProposer` implementations that request JSON patches from independently installed harness CLIs. They do not delegate source writes or test execution, and the core `run_coding_task` still enforces scoped Git patches and host-side tests.

## Codex CLI

`CodexCLIPatchProposer` uses a single `codex exec` invocation with `--sandbox read-only`, `--ask-for-approval never`, `--skip-git-repo-check`, `--output-schema` and `--output-last-message`. The process runs from an empty temporary working directory, not the target repository. The only repository source included is the operator-selected bounded text in the prompt.

Codex CLI may still access user configuration and provider credentials, and **read-only CLI sandbox flags do not establish OS-level isolation of an arbitrary installed binary**. The proposer is one-shot; unknown failures are never automatically retried.

There is currently **no host-verified hard USD cost cap** for native Codex CLI usage. A separate `--acknowledge-unmetered` flag is mandatory; do not use this adapter when hard provider billing limits are required. Use a budgeted JSON API transport instead.

## Claude Code CLI

`ClaudeCLIPatchProposer` invokes `claude -p` with `--bare`, `--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, `--permission-mode plan`, a one-turn limit, a JSON schema, and `--max-budget-usd`. Model output is untrusted and parsed against the exact patch schema.

Follow Anthropic's current commercial/API authentication rules: a third-party app must not extract or proxy Claude.ai login tokens or misuse subscriptions as API credentials. The user must approve a supported authentication arrangement. CLI-provided cost caps are additional defense-in-depth, not independent invoice certification.

## Optional CLI example

```bash
dbound code native-propose 'Fix VALUE' \
  --harness claude --model YOUR_CLAUDE_MODEL \
  --path /path/to/clean/repo --source-target app.py \
  --contract /path/to/contract.json \
  --image YOUR_PREPULLED_IMAGE --max-budget-usd 0.25 \
  --allow-native --allow-docker --approve-writes
```

For Codex, use `--harness codex --model YOUR_CODEX_MODEL --acknowledge-unmetered` in place of Claude's budget setting. Both require a separately installed and trusted native executable. The host owns all patch validation, test commands and receipts. This CLI executes a **single proposal**, not a multi-turn native harness worker and not an unattended code-merging agent.

## Contract testing and extension

Mock `subprocess.run` to assert read-only flags, no project cwd, JSON schema, limited environment forwarding and no retry on ambiguous exit. If a newer CLI changes flags or output, update the adapter and its tests after verifying official documentation. A failed external process returns a short error without copying stderr or source into durable traces.

For true native SDK and remote managed sessions, implement new explicit adapter interfaces with their own authorization, sandbox, cost and event-stream semantics. Do not silently rebrand these CLI wrappers as production-certified SDK integrations.

See [Repair loops](repair-loops.md), [Sandboxing](sandboxing.md), [SDK overrides](sdk.md) and [API reference](api-reference.md).
