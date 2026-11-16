# Long-running agents, coordination and crash recovery

DecisionBound's current background runner is a **local, single-device service primitive**. It can poll approved workflow graphs for extended periods, renew task leases while work is running, and resume from the SQLite ledger after a process or host restart. It does **not** operate in the ChatGPT conversation after a response has ended; an installed, independent OS service must stay running.

**Status:** implemented locally and tested with deterministic workers and a real `os._exit` process-crash/restart test. Running unattended for 72 hours, execution of real model-backed coding workers, distributed workers, and actual power-loss recovery remain **unverified**.

## How recovery works

```text
OS service manager (restart on failure / reboot)
             |
    dbound workflow serve
             |
  SQLite coordinator leader lease ← generation + owner token CAS
             |
  Project-scoped indexed queue
             |
  Workflow DAG → atomic worker claim → task lease heartbeat
                                    |
                           approved bounded worker
                                    |
                       host verification + receipt
                                    |
                         durable event journal
```

The authority is the persisted ledger, not the agent's memory, tool output, or a process-local thread. A restarted coordinator acquires leadership only after an expired leader is fenced, then reconciles workflow claims. Independent, idempotent, **zero-cost read-only** stages may retry when their approved attempt budget allows it. **Coding/writing and paid stages do not automatically replay** after uncertain termination: they remain blocked or failed for operator inspection. `task_completed` remains false for a verified *stage*.

## Run locally

Start from an initialized project and a user-owned state directory:

```bash
# Interactive session: a single sweep without touching a provider
DBOUND_HOME="$HOME/.decisionbound" dbound workflow serve --path . --once

# Continuous operation until signalled (explicitly opt in)
dbound workflow serve --path . --poll-seconds 2 --max-rss-mib 512

# Constrained laptop/low-RAM profile: conservative polling and one dispatch
dbound workflow serve --path . --low-resource

# Confirm whether a local leader is active (does not expose its token)
dbound workflow leader-status
```

Default worker permissions are deliberately narrow: **offline context preparation only**. `--allow-mock` also enables synthetic test tasks, which never establish real software-task completion. Real Claude/Codex/LLM/code workers must be installed, explicitly authorized and injected through the SDK with independent sandbox, budget and acceptance contracts. The background runner does not enable them automatically.

Signals request a graceful drain of in-flight work. A worker that hangs indefinitely cannot be forcibly killed by an in-process Python thread. A worker injected through the SDK as a `ContainedWorker` runs in its own process tree and can be killed: see [Worker containment](worker-containment.md). After the stop grace period (`worker_stop_grace_seconds`, default 10) a graceful stop cancels contained workers, and losing leadership cancels them at the next tick. Production coding and network workers that are not contained must still run in separately managed processes or containers with hard deadlines. `--max-seconds` restricts new scheduling but is **not a hard timeout for an already running child**.

After a coordinator crash, the next coordinator reaps the contained worker trees the old one left behind before it dispatches anything. `dbound workflow reap-orphans` does the same by hand.

## Example Linux systemd user service

Review paths, run as a dedicated least-privileged account, and keep provider credentials outside service arguments. A user service requires systemd user supervision; if it must survive logout, configure user lingering with your system administrator.

```ini
# ~/.config/systemd/user/decisionbound.service
[Unit]
Description=DecisionBound local workflow coordinator
After=default.target

[Service]
Type=simple
WorkingDirectory=/absolute/path/to/initialized/repository
Environment=DBOUND_HOME=%h/.decisionbound
ExecStart=%h/.local/bin/dbound workflow serve --path /absolute/path/to/initialized/repository --poll-seconds 2 --max-rss-mib 512
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.decisionbound

[Install]
WantedBy=default.target
```

**This is a reviewed starting template, not a tested systemd unit.** Sandboxing options and executable paths must be validated on the target distribution. Coding workers may require separately granted worktree access; do not weaken the coordinator's restrictions globally for that purpose.

On Windows, schedule a persistent logged-in user task using Task Scheduler's *restart on failure* and *start at logon* features. This project's hosted Windows/VS Code certification remains pending; no Windows service claim is made.

## Security and recovery invariants

| Risk | Current local behavior | Required next gate |
|---|---|---|
| Two coordinators claim one local state DB | Exclusive SQLite leader token and monotonically increasing generation | Competing-process crash and clock-change drills |
| Worker runs beyond initial claim TTL | Host renews task lease periodically while waiting. A `ContainedWorker` also has a hard deadline and its process tree is killed | Docker and Windows job object gates in CI. Thread workers remain unkillable |
| Coordinator killed while a contained worker runs | The next coordinator reaps the tree before dispatching, killing only a process whose start token matches | Real long-run drills. No lifeline pipe, so workers run until the next coordinator starts |
| Process killed midway through a read-only task | Expired zero-cost claims can retry within `max_attempts` | Extended kill/restart soak |
| Paid or write-capable task interrupted | Uncertain outcome quarantined; no automatic replay | Audited reconciliation workflow and external idempotency receipts |
| Memory use grows | RSS admission check before each dispatch; fixed queue batches | Whole-process/container memory ceiling and representative RSS/CPU benchmarks |
| Workflow cross-project leakage | Indexed `project_id` queue filtering | Authenticated remote tenant isolation |
| Laptop shut down | No work executes until service restarts | Platform startup/restart validation |
| Cloud/device outage | No remote durable queue implemented | PostgreSQL/outbox design and distributed fencing |

## Quality gates and endurance plan

1. **G16 Local 24–72 hour soak:** scripted worker/provider failures, monotonic memory growth checks, no lost events, bounded service CPU at idle, safe signal stop and `SIGKILL` restart; record versions, p50/p95/p99, RSS and recovery delay.
2. **G17 Worker isolation:** hard process kill, sandbox policy enforcement, failure to terminate escalation; no thread-only timeout claims. *Partially met:* hard deadline, tree kill, orphan reaping and fail-closed termination are implemented and tested on Linux in [Worker containment](worker-containment.md). Open: Windows job object and macOS results from CI, the Docker gate, and an external security review.
3. **G18 Multi-host orchestration:** PostgreSQL transactional queue and immutable receipts; lease generations, remote worker auth, per-tenant budgets, single-writer outbox and split-brain tests. SQLite cannot serve as distributed consensus.
4. **G19 Scale/resources:** measure idle CPU/RSS, open descriptors, startup and task latency, provider token cost per verified outcome, adaptive queue polling and backpressure. Set measurable gates before launch.
5. **G20 Production security/operability:** supply chain, signing and rollbacks, rotating credentials, least-privilege execution, independent audit, runbooks, SLOs and incident drills.

Implementation and evidence are tracked in [Production readiness](production-readiness.md) and the security policy. Targeting thousands of devices is a product ambition, **not** a tested scalability result.

**Low-resource mode:** `--low-resource` caps admission at 256 MiB RSS, schedules one new task per workflow pass and polls no faster than every five seconds. It does not enforce a hard process memory limit or reduce Python interpreter overhead. Untrusted workers need container/cgroup or Windows Job Object resource enforcement.

### Performance probes and long-duration certification

Use `python scripts/profile_background.py --workflows 3 --tasks 5` for bounded synthetic preparation-stage throughput, or `python scripts/soak_background.py --seconds 300 --poll-seconds 2` for an isolated five-minute idle CPU/RSS reading. For operator-host endurance, `--seconds 86400` or `259200` requests a 24/72-hour finite test; this has **not** been run in the authoring environment. The script uses a temporary project and local database, no credentials, providers, shells, or coding workers. Record actual OS, Python version, service manager logs, CPU/RSS trajectories and failure injections before promoting the endurance gate.
