# DecisionBound alpha security policy

**Status: experimental `0.13.0a1`.** This repository contains an operational local SDK, CLI, opt-in provider adapters, local workflows and a loopback API. It has **not** received independent security review and is not approved for unattended access to sensitive production repositories.

## Supported versions and reporting

Only the latest main/alpha checkpoint is actively maintained; there is no stable or LTS release. Report sensitive vulnerabilities privately at https://github.com/ahmedjawedaj/decisionbound/security/advisories/new. Include affected commit/version, impact and a minimal reproduction without live credentials or private client data. Use public issues only for non-sensitive defects. No response-time SLA is promised.

## Runtime defaults

- Remote model requests are **disabled unless explicitly enabled**. The CLI requires `--allow-remote`, an environment credential, and explicit token-price assumptions. There are no automatic retries after ambiguous provider calls.
- Remote HTTPS provider endpoints must use an approved host; no URL credentials, redirects, or unapproved IP literals. Custom endpoints are an explicit trust decision. Network egress and DNS protection should also be enforced at infrastructure level.
- The model can select only offered read-only tools. Host-side strict argument validation and effect permissions are independent of the model's output.
- File inspection refuses hidden names, excluded directories (`node_modules`, `.venv` and similar, in any case), secret-like file names (for example `credentials.*`, `secrets.json`, `id_rsa*`, `*.pem`, `*.key`, `*.env`, `service-account*.json`, `kubeconfig`, matched in any case), names with a trailing dot or space or a colon, unsupported types, large files, symlinks, and traversal outside the initialized repository. This is a **bounded source inspection policy**, not a process sandbox. Non-hidden source files can still contain secrets; treat model data sharing as an explicit user decision.
- The local HTTP control API is loopback-only and uses a stored bearer token; it rejects untrusted Host/Origin requests. Do not expose it to the internet or another tenant.
- The MCP server uses local stdio and is bound to one verified project root. The MCP host inherits the operating-system user's read access. Authentication for remotely hosted MCP endpoints is **not implemented**.
- Events record metadata and hashes rather than raw provider prompts, tool output, file excerpts or credentials. Returned CLI/API responses may contain source excerpts and should be handled as sensitive.
- `DockerTestRunner` refuses a root caller before contacting Docker or Podman. It runs as the caller's POSIX UID/GID, or `1000:1000` on platforms without those IDs. Run the client as a non-root user. This check does not certify daemon isolation or prevent container escapes. The runner pins the `docker` context to `default`, refuses a non-`unix://` `DOCKER_HOST`, and records the image, its `sha256:` digest (empty for a tag) and the daemon endpoint in the test receipt and its hash. These are not in the persisted ledger events. The pin moves a setup that selects its daemon only with `docker context use` to the default socket. That includes rootless Docker, colima, the Docker Desktop `desktop-linux` context, lima and remote contexts. A run there does not say that the context was ignored, and usually looks like an ordinary test failure or a failure to connect to the daemon. To keep using such a daemon, set `DOCKER_HOST=unix://<socket path>` for the process. It is honoured and the pin is not applied over it. Only a local `unix://` socket is accepted, so a remote context cannot be used. `daemon_endpoint` is the configured intent and not the observed daemon. It is the literal `default` or the `DOCKER_HOST` string, which can embed a user ID, and for `podman` it records `DOCKER_HOST` even though Podman ignores that variable. The strict digest switch is read once, when the runner is constructed, so changing the environment later does not affect a runner that already exists. `DBOUND_REQUIRE_IMAGE_DIGEST=1` refuses an image not pinned by digest.

## Production blockers

No independently certified container/OS sandbox for arbitrary execution, no permission for executing model-generated shell commands, no automatic merge, remote multi-tenant auth/SSO, model-provider live integration certification, dependency audit or SBOM exists yet. Private vulnerability reporting is enabled on GitHub; it is not a security certification. A passed unit test does not eliminate prompt injection, malicious repository content, compromised provider libraries or tool-runner attacks.

## Local security validation

```bash
python -m pytest -q tests/security tests/contract tests/unit tests/integration
```

Threat cases cover credential gating, model output injection into tool names, denied side effects, traversal/symlink escapes, receipt tampering, model budgets, and host/origin rejection. The official MCP v2 wire test needs the optional SDK and runs in a dedicated CI job.

The original project is Apache-2.0. Private vulnerability reporting is the preferred disclosure channel; never include credentials or confidential client code in public issues.

## Sprint 05 policy, recovery and native process boundaries

- Operator-reviewed acceptance contracts pin exact test commands and changed-file scope; the model cannot weaken them. Passing a contract does not imply that every human requirement is fulfilled.
- Repair sessions are single-user SQLite journals with serialized idempotency/attempt reservations. A crash before cost reconciliation retains a pessimistic approved spend reservation; ambiguous writes cannot be auto-replayed. This is not a distributed exactly-once executor.
- Native Codex/Claude patch-proposal shims launch separately installed subprocesses in a temporary directory with bounded input and minimal forwarded environment. They are not a security sandbox for an untrusted native executable or a complete SDK integration. Codex CLI costs are not hard-capped by this adapter.
- Real Docker isolation, official MCP v2 protocol, provider billing and native process correctness need connected-host review. Never claim test mocks establish live security or code correctness.


## Sprint 08 local review authorization

Human review lives behind a local OS-user CLI or paired-loopback browser token. `ReviewGate` checks completed coding attempts, clean source HEAD, bounded text-only patch bytes, host receipts, contract hash and review attestation. Approval is SHA-256 snapshot and CAS-revision bound; mocked tests require a separate explicit acknowledgment. `export` checks the current approval and creates a private patch file outside the source checkout, but cannot apply/merge/deploy anything. The MCP server exposes only read-only metadata for its initialized project; no diff or approval/export tools.

There is **no remote user identity, SSO, team RBAC or certified multi-tenant access control**. Local OS-user permissions and the paired API token define the operator boundary; a compromised process running under the same OS user can alter local state. No browser endpoint performs patch export or source writes. The current browser DOM test uses a mocked HTTP layer because localhost navigation is blocked in this authoring environment; HTTP API tests use FastAPI TestClient. See `site_docs/review-console.md`.

## Local SQLite durability and backup boundary (Sprint 12)

`dbound state backup` uses SQLite online backups with full integrity checking and
non-overwriting atomic file publication. The output is sensitive; it may hold
project names, task descriptions and private event metadata. It must remain
outside source control and should be encrypted before transfer to external
storage. `dbound state restore` creates a separate file, never replaces active
WAL state, and does not imply trust in any worker-generated result. No
cross-device replication or remote authentication is introduced.
