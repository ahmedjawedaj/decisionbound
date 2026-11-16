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
| Leaked provider credentials | Built-in profiles carry no keys | Environment/keychain integration pending |
| Unapproved cross-project memory | Scope, owner and explicit tag checks | Does not yet support enterprise identity or policy reconciliation |
| Unauthorized localhost browser requests | Token, Host and Origin checks | No remotely deployed OAuth/SSO; local process compromise out of scope |
| Duplicate write after crash | Only read-only worker operations shipped | Durable write reconciliation remains future work |
| Unsound model verification | Current verifier only checks preparation | Independent code/test artifact verifier pending |

## Security boundaries for contributors

Do not use model prompts as authorization. Do not elevate candidate memories to approved policies automatically. Do not use a public GitHub Pages site to receive or store developer secrets. Never publish private repository content, unreleased code or protected benchmark fixtures as documentation.

For a vulnerability report, follow the project's `SECURITY.md` contact policy; do not post active secrets in a public issue.

## Coding-agent write path

See [Sandboxing & threat model](sandboxing.md) and [Coding agents](coding-agents.md). Model-proposed diffs are untrusted, write consent is required, and the host enforces strict Git patch/path policies. Real untrusted tests require opt-in, network-disabled Docker isolation. The repository is not a certified multi-tenant code-execution service.
