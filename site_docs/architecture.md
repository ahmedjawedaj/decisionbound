# System architecture and boundaries

DecisionBound is built as a **small auditable core** with replaceable ports. Runtime APIs and tools cannot elevate repository content to a security policy.

```text
CLI (Typer) ──┐
             ├─> Application services ──> DecisionRepository (SQLite default)
Web (FastAPI)┘           │                       └── durable event ledger
                        ├── TransferPolicy (conservative default)
                        ├── ContextCompiler (budgeted lexical default)
                        ├── Skills registry (lazy, untrusted content)
                        ├── AgentWorker (offline preparation default)
                        ├── JSONModelTransport (optional; user-enabled)
                        ├── BudgetedModelPlanner (strict JSON + budgets)
                        ├── ToolRegistry (read-only, least-privilege)
                        ├── Official MCP v2 server (optional stdio)
                        └── TaskVerifier (tool-receipt checker)
```

## Trusted vs. untrusted inputs

**Trusted only after authorization:** project registry, human-approved decisions, separately configured organization policy, allowed tool names and operation scope. **Untrusted until checked:** ADR text, Git history, downloaded skills, MCP descriptions, model outputs, arbitrary source files and imported profiles.

Remote model calls are disabled by default and require CLI opt-in, an environment credential, explicit prices and a known endpoint. The only model-driven agent shipped is a read-only repository inspector. Autonomous coding remains outside this alpha.

## Transfer authority

1. Unapproved or revoked decisions always abstain.
2. Owner-supplied forbidden decision IDs escalate for review.
3. Project-scoped rules apply only to the source project.
4. Personal preferences can transfer only to a target with the same declared owner.
5. Required technology tags must all match; blocked tags cause abstention.
6. Cross-project personal transfer produces `translate`, not unconditional `apply`.

This is an intentionally **conservative baseline**, not proof of intelligent semantic transfer. Evaluate any model-based replacement against the published contract tests before using it in sensitive repositories.

## Persistent state and event replay

SQLite uses WAL, parameterized SQL, revision checks, a declared schema version, and a durable append-only run event table. It is suitable for a local single-user alpha, **not a distributed queue or multi-tenant cloud service**. Concurrent modified approval or profile revisions fail rather than silently overwriting another client.

## Runtime lifecycle

`task → compile context → emit started event → worker.run → emit result → verifier.verify → emit verification`.

If a worker raises, an error event is written. Completion is **never inferred** from a worker result alone; the current preparation verifier is intentionally not an end-to-end artifact verifier.

## Future components (not yet implemented)

Real coding workers, multi-agent supervisor/dispatcher, native Codex/Claude SDK adapters, arbitrary external MCP **client** orchestration, enterprise identity, PostgreSQL coordination and production sandboxing. Their design documents exist in the archived v1.5 planning baseline; they must not be advertised as shipped capabilities.
