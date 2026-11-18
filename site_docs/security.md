# Security, privacy and limitations

## Default guarantees of this alpha

- No API key is required to run local commands.
- No automatic network calls, paid model requests, remote telemetry or code writes.
- Local web API binds loopback only and requires a per-user bearer token for state access.
- Imported profiles reject unknown keys and obvious credential-key fields; **never rely on this as a comprehensive secrets scanner**.
- Source evidence paths must stay inside the project root, including symlink resolution.
- Database files reject symlink targets and use local POSIX restrictive permissions when possible.
- Approval requires evidence hash equality; unknown changes require rediscovery and review.
- Tool calls require allowlisted names and an explicit write grant if they have side effects.

## Threat model

| Threat | Current control | Remaining limitation |
| --- | --- | --- |
| Prompt injection from ADR or skill | Treated as data, never automatic policy | A future model tool loop needs stronger sandboxing and adversarial tests |
| Prompt injection through tool output (text inside a repository file) | The model planner wraps each tool result between `<<<UNTRUSTED_TOOL_OUTPUT>>>` and `<<<END_UNTRUSTED_TOOL_OUTPUT>>>`, breaks any run of three or more `<` or `>` inside the content so it cannot fake a marker, names the tool outside the markers, and tells the model the text is data. A cut result carries a note after the closing marker | This is marking, not a defence. It does not show that any model resists injection, and no adversarial run with a real model has been done. Tool permissions are the real control |
| Planner stuck repeating one tool call | After `max_identical_calls` identical calls in a row (default 3) the next one raises `RepeatedCallError` before the tool runs | Only identical consecutive calls are caught. A planner that alternates two calls is stopped only by the step budget |
| Run that takes too long | Optional `deadline_seconds` stops the loop before the next planner or tool call and raises `AgentDeadlineExceeded` | Off by default. It cannot interrupt a planner or tool call that is already running, and a `Finish` returned after the deadline is still accepted |
| Leaked provider credentials | Built-in profiles carry no keys | Environment/keychain integration pending |
| Unapproved cross-project memory | Scope, owner and explicit tag checks | Does not yet support enterprise identity or policy reconciliation |
| Unauthorized localhost browser requests | Token, Host and Origin checks | No remotely deployed OAuth/SSO; local process compromise out of scope |
| Hung or runaway contained worker | Hard deadline, process tree kill, resource limits, bounded output, orphan reaping on restart | Not a sandbox: `setsid` and job breakaway escape, no filesystem or network isolation. Tested in CI on Linux, macOS and Windows. See [Worker containment](worker-containment.md) |
| Duplicate write after crash | Only read-only worker operations shipped | Durable write reconciliation remains future work |
| Unsound model verification | Current verifier only checks preparation | Independent code/test artifact verifier pending |

## Agent loop stop reasons

The read-only loop emits a `loop_stopped` event with `reason` (`repeated_call`, `deadline` or `step_budget`), `steps_run`, and for a repeated call the tool `name`. It never carries arguments or tool output. All three stops raise a `RuntimeError` subclass or `RuntimeError` itself, so existing handlers keep working. Stored observations keep the shape `{"tool": ..., "result": ...}` that the evidence verifier parses and whose size and hash the ledger verifier checks, and the tool name comes first so a cut observation still shows it. The markers are added only when the planner builds its request, so stored sizes and hashes are unchanged. They add about 75 estimated input tokens per observation shown (about 300 for the four the planner sends), which counts against the planner's input bound.

## Security boundaries for contributors

Do not use model prompts as authorization. Do not elevate candidate memories to approved policies automatically. Do not use a public GitHub Pages site to receive or store developer secrets. Never publish private repository content, unreleased code or protected benchmark fixtures as documentation.

For a vulnerability report, follow the project's `SECURITY.md` contact policy; do not post active secrets in a public issue.

## Coding-agent write path

See [Sandboxing & threat model](sandboxing.md) and [Coding agents](coding-agents.md). Model-proposed diffs are untrusted, write consent is required, and the host enforces strict Git patch/path policies. Real untrusted tests require opt-in, network-disabled Docker isolation. The repository is not a certified multi-tenant code-execution service.
