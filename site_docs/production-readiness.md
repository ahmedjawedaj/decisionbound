# Production readiness and fleet scale

DecisionBound targets serious developer use, but **the current release is an experimental single-user alpha**. Supporting thousands of installations requires far more than passing local tests or scaling a single agent's request loop.

Production readiness requires independent evidence for packaging, state durability, platform behavior, containment and security.

## Three distinct deployment models

| Model | What it means | Status |
|---|---|---|
| Thousands of independent local installations | Each user runs their own CLI and private local database; no shared accounts required | Basic architecture supports separate local state, but real fleet-scale install/upgrade and OS certification are pending |
| Connected user devices | CLI, browser and editor share events on one machine; future devices can sync through a managed account | Loopback/local sharing tested; **cross-device sync not implemented** |
| Centralized multi-tenant agent service | Users, organizations, remote workers, credentials and policies isolated on authenticated infrastructure | **Not implemented**; requires PostgreSQL, tenant isolation, remote authentication, signed updates and security review |

## Reliability prerequisites

A device should survive interruption without inventing success, repeating uncertain writes or losing approved decision history.  adds [consistent state backups and recovery](state-recovery.md), but does not replace an incident-response runbook, filesystem and power-loss drills, end-to-end encrypted backups, online activation and rollback of staged schema migrations, or remotely durable replication.

Other critical requirements: policy-controlled agent execution, real container isolation, versioned MCP capabilities, provider-specific budget certification, auditable approval boundaries, reproducible research baselines and true OS/VS Code tests. A release remains blocked if the corresponding connected test did not execute.

## What we will measure

Per-version, per-OS clean-install pass rates; setup success and time; startup latency and memory; workflow throughput and tail latency; event-log integrity; data-loss incidents; recovery-point/recovery-time objectives; model spend per verified task; unsafe action rate; transfer/abstention accuracy; and support burden. Targets must be defined before measurements, not reverse-engineered from successful demos.

## Open-source governance

Original project material is Apache-2.0. GitHub Pages is a static documentation site, not the local API or agent runtime. Public releases need signed source/build artifacts, reproducible CI, a reviewed dependency/security policy, contributor-facing docs and verified security response processes.

[State backup & recovery](state-recovery.md) · [Host certification](host-certification.md) · [Research](research.md) · [Roadmap](roadmap.md)

## Durable background services and low-resource operation

**G17 worker isolation (partial).** Contained workers have a hard deadline, process tree termination, cancellation across processes, orphan reaping after a coordinator crash, process id reuse protection and fail-closed termination. These are tested with real child and grandchild processes on Linux. Still open before G17 can close: Windows job object and macOS results from the CI matrix, the Docker gate, a long-run drill, and an external security review. In-process workers are not covered. Details: [Worker containment](worker-containment.md).
