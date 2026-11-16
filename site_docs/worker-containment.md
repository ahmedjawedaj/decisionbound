# Worker process containment

`ContainedWorker` runs a task in a real child process that DecisionBound can kill, together with everything the child started. It gives hard deadlines, cancellation that works across processes, and cleanup after a coordinator crash.

**Status:** implemented and tested with real child and grandchild processes on **Linux only**. The Windows job object path and the macOS identity path were written from platform documentation and have not been executed by the authors. Gate G17 stays open until the CI matrix, a Docker gate and an external security review report results.

**Claim limit:** hard deadline and tree termination for contained workers on Linux, with the documented escapes below. macOS and Windows make no claim until CI results exist. This is not a security sandbox.

## What you get

| Situation | What happens |
|---|---|
| The worker runs past its deadline | The whole process tree is killed and the task fails with `WorkerDeadlineExceeded`. |
| `dbound workflow cancel` runs in another process | The claim is revoked, the supervisor notices at its next tick (at most half a second) and kills the tree. |
| The worker exits or crashes and leaves a grandchild | The grandchild is killed when the worker exits. |
| The worker prints a receipt while it is being cancelled | The receipt is discarded. The task stays cancelled. |
| The worker prints more than `max_output_bytes` | The tree is killed and the task is quarantined as `invalid_receipt`. |
| The coordinator loses leadership | Workers are cancelled at the next tick. |
| The coordinator is killed with `SIGKILL` | Workers keep running until the next coordinator starts. It reaps them before it dispatches anything. |
| Termination cannot be proven | The task is quarantined as `containment_failed` and never retried, even for read-only kinds. |
| A `code` task on a host with only weak containment | The task is refused before anything is spawned. |

## Using it

Contained workers are injected through the SDK. `dbound workflow serve` still registers only the offline `PreparationWorker`.

```python
import sys
from decisionbound.containment import ContainedWorker, ProcessRegistry, ResourceLimits

worker = ContainedWorker(
    [sys.executable, "-I", "/opt/tools/worker.py"],  # fixed argv chosen by the operator, use an absolute path
    registry=ProcessRegistry(store),
    deadline_seconds=300,
    term_grace_seconds=3,
    limits=ResourceLimits(max_cpu_seconds=120, max_open_files=256),
    env_passthrough=("MY_TOOL_CONFIG",),
)
supervisor = WorkflowSupervisor(journal, workers={"inspect": worker})
```

The child runs with the project as its working directory, so a relative script path would load code from the project. Always use an absolute path. The argv you choose decides what the child can do. DecisionBound does not grant write, model or network permission to a contained worker.

## Worker protocol

- **stdin:** one JSON object with `workflow_id`, `project_id`, `project_path`, `task`, `attempt`, `reserved_cost_usd` and `handoffs`. It carries no owner token and no generation.
- **stdout:** exactly one JSON line with `status` and optionally `evidence_sha256`, `repair_session_id`, `nested_run_id` and `cost_usd`. Anything else is an invalid receipt. The host still verifies the receipt independently.
- **stderr:** discarded.
- **Environment:** an allowlist. `PATH` (plus `SYSTEMROOT`, `TEMP` and `TMP` on Windows) and the names in `env_passthrough`. Nothing else from the parent leaks.
- **Working directory:** the project path.

## Deadlines, cancellation and outcomes

The deadline is monotonic and starts at spawn. It is independent of the lease. Heartbeats continue until the deadline. The worst case from deadline to confirmed death is the deadline, plus one 0.5 second tick, plus `term_grace_seconds`, plus up to five seconds of confirmation.

Cancellation always revokes the claim first. The supervisor sees the failed heartbeat and cancels the worker, so cancellation works when the request came from another process.

| Outcome | Task result |
|---|---|
| Deadline, crash or cancel while the lease is valid | `fail`. A `code` task is blocked and never replayed. Zero-cost read-only kinds may retry within `max_attempts`. Everything else fails. |
| Invalid or oversized receipt | Quarantined as `invalid_receipt`. |
| `ContainmentFailure` | Quarantined as `containment_failed`. If the claim already moved on, the task at that generation is blocked. |
| Lease expired for a `code` task | Unchanged: blocked as `unknown_write_outcome`. |

Budget reservations stay charged after a kill.

A worker that returns after its claim was lost cannot finish the task, because `finish` and `fail` compare owner token, generation and lease expiry. The supervisor records a `late_result_rejected` event with the stale generation.

## Process model per operating system

**POSIX (Linux executed, macOS not executed).** Every spawn goes through a small stdlib-only launcher that applies resource limits and then `exec`s your argv in a new session. This avoids `preexec_fn`, which is unsafe in a threaded coordinator. Termination is `SIGTERM` to the process group, a grace period, then `SIGKILL`, then confirmation that no live process remains in the group. The worker's exit is observed without reaping it, so the group is killed before the process id can be recycled. Where `os.waitid` is missing (macOS), the leader is reaped first, which leaves a tiny recycling window.

**Windows (not executed).** A job object named `Local\dbound-<id>` with kill-on-close. The launcher waits on a one byte gate on stdin while the parent assigns it to the job, so no descendant is born outside the job. Termination is `TerminateJobObject`, which has no graceful phase. If a job cannot be created or assigned, the fallback is `taskkill /PID /T /F`, reported as `windows_tree_best_effort`. That fallback misses re-parented descendants, so it is refused for `code` tasks and whenever `require_strong` is true.

## Resource limits

| Limit | Linux | macOS | Windows |
|---|---|---|---|
| `max_cpu_seconds` | `RLIMIT_CPU` | `RLIMIT_CPU` | job time limit |
| `max_memory_mib` | `RLIMIT_AS` | not enforced | job memory limit |
| `max_open_files` | `RLIMIT_NOFILE` | `RLIMIT_NOFILE` | not enforced |
| `max_processes` | not enforced (`RLIMIT_NPROC` is per user) | not enforced | active process limit |
| `max_output_bytes` | host side | host side | host side |

Core dumps are disabled on POSIX. A requested limit the host cannot enforce makes a strong request fail closed. Every spawn records the enforced limits in a `worker_started` event.

## Orphans, restart and process id reuse

Each worker gets a row in the `contained_processes` table before it is spawned. The row holds process identity and claim references only. It never holds argv, environment, task text, tokens or output. The write order is intent row, spawn, then pid and identity.

A process identity is the pid plus a start token: Linux uses `/proc/<pid>/stat` field 22 with the boot id, macOS uses the kernel start time, Windows uses the creation time.

When a coordinator acquires leadership it runs the reaper before its first dispatch. A row is an orphan when its owner is dead or when it was started under an older leadership generation. Rows started outside a coordinator have no generation and are only judged by their owner.

| Finding | Action | Row state |
|---|---|---|
| Live process, same start token | Kill the tree | `terminated` |
| Live process, different start token | Send no signal | `identity_mismatch` |
| Leader gone, group empty | Nothing to do | `gone` |
| Leader gone, group still alive (POSIX) | Send no signal, block the task | `unconfirmed` |
| Crash between intent and spawn | Block the task | `unconfirmed` |

The reaper never changes task state except to block an unconfirmed task. The journal stays the only authority. You can run it by hand with `dbound workflow reap-orphans`, which takes the coordinator lease so it cannot race a running coordinator.

## What is not guaranteed

- A POSIX descendant that calls `setsid` or `setpgid` escapes the group.
- A Windows process that breaks away from the job, or starts through WMI or a service, escapes the job.
- The `taskkill` fallback misses re-parented descendants.
- There is no filesystem, network, credential or syscall isolation. No cgroups, namespaces, seccomp or AppContainer. Use the [Docker coding sandbox](sandboxing.md) for that.
- There is no lifeline pipe or subreaper. After a coordinator `SIGKILL` on POSIX, workers run until the next coordinator reaps them.
- A crash between spawn and the pid record can leave an unrecorded process on POSIX. Windows is covered by job close.
- Existing in-process workers (`PreparationWorker`, `CodingRepairWorker`) were not converted. They are still not killable.
- A descendant that escapes the group and keeps the worker's stdout open is not detected. The run can finish while that process lives on.
- `RLIMIT_CPU` and `RLIMIT_AS` apply per process, not to the whole tree.
- A `WorkflowSupervisor` used without the coordinator never reaps orphans. Call `dbound workflow reap-orphans` or run the coordinator.
- On hosts without `ps`-grade start times (the macOS `ps` fallback) the identity token has one second resolution.
- The registry table is never pruned.
- Cancelling a worker discards its receipt. A cancel induced `WorkerCancelled` does not emit a `late_result_rejected` event.
- A supervisor error cancels running workers, which are then killed. The lease must exceed the SQLite busy timeout, otherwise a blocked heartbeat can cost a worker its lease.
- A host that ignores `SIGCHLD` is unsupported.
- There is no distributed fencing. This is one machine and one SQLite database.

See also [Durable background service](durable-service.md), [Security](security.md) and [Production readiness](production-readiness.md).
