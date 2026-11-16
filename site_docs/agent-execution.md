# Model-driven repository inspection

DecisionBound 0.4 alpha includes an **opt-in, read-only** LLM planner. This remains a separate **read-only inspection path**, not a full Claude Code/Codex harness or a multi-agent dispatcher.  adds a limited [consent-gated coding worker](coding-agents.md) with disposable worktrees and container tests.

## The execution contract

```text
User task / initialized project
     │  evidence and ownership checks
     ▼
DecisionService.prepare → immutable ContextCapsule
     │  choose only explicitly allowed tool descriptions
     ▼
BudgetedModelPlanner → JSON action with strict envelope
     │  parse, whitelist and type validation (host-side)
     ▼
BoundedReadOnlyAgent → repository_readonly_tools
     │  search_project | read_file only
     ▼
ReadOnlyEvidenceVerifier → audited receipt
     │
     └── evidence_inspection_verified ≠ task_completed
```

The host, never the language model, controls permissions. Tool descriptions are lazily filtered for the run; tool output is untrusted. The loop refuses write tools, shell execution, arbitrary URLs, source-code modifications, and unauthorized filenames.

## Offline demo

```bash
python -m pip install -e '.[test,web,docs]'
cd /path/to/repository
# Initialize only once
dbound init --technologies python
# The demo uses preprogrammed model-shaped responses; zero network requests
dbound agent demo 'verify_payment' --path .
```

The demo performs search, prepares a final explanation, writes metadata-only audit events, and **always returns `task_completed: false`**.

## Real provider (explicit opt-in)

```bash
export OPENAI_API_KEY='...'
dbound agent run 'Find the refund authorization checks' \
  --path . --provider openai --model YOUR_COMPATIBLE_MODEL \
  --allow-remote --max-cost 0.15 \
  --input-price 1.00 --output-price 4.00 --max-steps 4
```

Or use `--provider anthropic` plus `ANTHROPIC_API_KEY`. Price flags are illustrative, **not current provider prices**. User must supply their actual model's rates. Credentials are loaded only from environment variables and are not written to profile YAML or the event ledger. Calls are not retried after ambiguous failures. The default `dbound run` command remains fully offline.

### Provider caveats

- OpenAI-compatible adapter uses Chat Completions JSON-schema response format. A particular compatible gateway/model may reject it; user testing is required.
- Anthropic Messages adapter requests JSON but does not claim provider-native strict schema enforcement. Host-side JSON and tool validation applies to both.
- The transport permits only approved HTTPS provider hosts (or explicitly enabled loopback), rejects authentication in URLs, and disallows redirects. Organizational firewalls should still enforce network egress.
- These adapters are **model text-generation adapters**, not native Claude Agent SDK or Codex SDK execution adapters.
- Live-provider conformance and pricing must be verified by the user. Mock tests cover the HTTP envelope, refusals, and token receipts.

## Action envelope

The model's **entire** response must be a JSON object with exactly these four fields. For strict JSON schema portability, `arguments_json` itself is a JSON-encoded object string.

```json
{"kind":"tool","tool":"search_project","arguments_json":"{\"query\":\"refund\"}","message":""}
```

A terminating action has `kind="finish"`, `tool=null` and `arguments_json="{}"`. It may include a human-readable explanation, but that explanation is **not** an independent success signal.

## Budgets and accounting

`ModelLimits` constrains calls, estimated input tokens, maximum provider output tokens, total accounted tokens, estimated USD cost, and an elapsed-time deadline. The planner reserves the worst-case calculated price before each provider call. Failed or timed-out requests retain their reservation because the provider might still bill them. If token usage is returned, it replaces the reservation; an overrun fails the run.

Preflight token counting uses a **conservative character heuristic, not a provider tokenizer**. This is not a financial hard guarantee across all providers or a timeout of arbitrary blocking I/O: for production, put quotas and timeouts in the provider gateway too.

## Audit and data handling

The SQLite run ledger stores event types, token/cost metadata, tool names, argument-key lists, sizes, and SHA-256 hashes. It intentionally omits raw tool output, full model responses, and provider credentials. The directly returned run receipt can contain excerpts from repository files; protect it like other source data.

## Override contracts

See [SDK integration](sdk.md) and [generated API reference](api-reference.md). Implement `JSONModelTransport.complete` and provide a new `BudgetedModelPlanner` for each run. Reuse `run_readonly_agent` to preserve host authorization and verification; replacing a planner does **not** grant a new tool permission.

## Validated scope

Local tests cover malformed model responses, unexpected action fields, unoffered tools, write attempts, cost exhaustion, provider token overruns, missing credentials, malicious paths, and persisted traces. No statement about research improvement or live-provider correctness follows from these tests.
