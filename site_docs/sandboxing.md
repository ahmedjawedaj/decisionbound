# Sandboxing, isolation and failure boundaries

This page describes the **implemented**  controls and their limits. A Git worktree isolates source changes, not process execution. A Docker container can constrain tests, but a Docker daemon, image provenance and host kernel remain trusted components; this is not a hardened multi-tenant isolation service.

## Trust boundaries

| Element | Trust level | Allowed responsibility |
|---|---|---|
| User / operator | Explicit consent | Approve patches, authorize remote model calls and sandbox tests |
| Model / patch proposer | Untrusted | Produce a text patch and explanation; never grant permission |
| Source files and skill text | Untrusted data | Supply bounded context, never override policy |
| Host patch validator | Trusted local code | Enforce patch syntax, path/mode restrictions and Git preflight |
| Detached worktree | Disposable filesystem | Receive isolated writes, never change tracked files in original checkout |
| Docker test backend | Constrained but not certified | Execute selected commands without network, privileged mounts or credentials |
| Local event ledger | Authoritative receipt metadata | Store hashes/counters/status; no raw source or model output |
| Independent verifier | Trusted host code | Compare receipts; reject missing or changed evidence; never assert general task completion |

## Process containment is not isolation

[Worker containment](worker-containment.md) can kill a worker's process tree on a deadline, a cancel or a lost leadership, and it reaps trees a crashed coordinator left behind. That bounds how long a worker runs. It does not restrict what the worker can read, write or reach on the network. Descendants that leave the process group or job object escape it. Filesystem, network and credential isolation stay with the Docker path.

## Threat-oriented safeguards

- **Patch points to `../secrets` or `.env`:** rejected by the parser before any Git write.
- **Patch modifies symlink or submodule:** parser/path/index-mode inspection rejects it.
- **Model creates a test that claims to pass:** only the configured host-owned test runner may produce test receipts.
- **Worker fabricates `kind=docker` in an override:** custom runners cannot pass the host acceptance gate.
- **Tests mutate extra or ignored files:** host inspects the entire worktree status after running and refuses acceptance.
- **Unapproved write:** no worktree or patch operation begins.
- **Provider timeout after a paid call:** no automatic retry; preflight reservation remains accounted.
- **Crash after worktree creation:** durable record permits operator reconciliation and explicit cleanup.
- **Patch artifact overwritten/tampered:** export compares saved bytes and length with ledger SHA-256; exclusive destination creation.
- **Docker command times out:** the runner attempts to remove the uniquely named container; ambiguous cleanup is treated as a failure.

## Limitations that must stay visible

- No Docker daemon is available in the build sandbox; the real Docker test is configured to run on connected GitHub Actions and is currently unverified in this environment.
- Local Git worktree operations use the installed Git binary; configured checkout filters are refused, but a full Git supply-chain audit is not included.
- Docker rootless mode, seccomp policies, image signatures, CVE policies, resource governance and host kernel security are operator responsibilities. Image pull is disabled during execution; images should be pre-pulled and ideally digest-pinned. A tag-only image is accepted by default, and then the receipt has an empty `image_digest` and cannot show which content ran. Set `DBOUND_REQUIRE_IMAGE_DIGEST=1` (or pass `require_digest=True`) to refuse an image that is not pinned by `sha256:` digest. `image`, `image_digest` and `daemon_endpoint` appear in the in-memory test receipt, in the `tests` list of the CLI JSON output and in the receipt hash. The persisted ledger events `code_test_receipt` and `code_prepatch_test_receipt` carry only the receipt hash, `kind`, `exit_code`, `timed_out` and `command_sha256` (the pre-patch event also `output_truncated`), so the ledger does not hold the three fields. The runner does not check that a pinned digest is the image you reviewed, and it does not identify the daemon beyond its endpoint. The runner builds the CLI environment itself, pins the `docker` context to `default` and refuses a non-`unix://` `DOCKER_HOST`. The context pin follows the Docker CLI's documented rules and was not run against a real daemon here. The pin moves a setup that selects its daemon only with `docker context use` to the default socket. That includes rootless Docker, colima, the Docker Desktop `desktop-linux` context, lima and remote contexts. A run there does not say that the context was ignored, and usually looks like an ordinary test failure or a failure to connect to the daemon. To keep using such a daemon, set `DOCKER_HOST=unix://<socket path>` for the process. It is honoured and the pin is not applied over it. Only a local `unix://` socket is accepted, so a remote context cannot be used. `daemon_endpoint` is the configured intent and not the observed daemon. It is the literal `default` or the `DOCKER_HOST` string, which can embed a user ID, and for `podman` it records `DOCKER_HOST` even though Podman ignores that variable. The strict digest switch is read once, when the runner is constructed, so changing the environment later does not affect a runner that already exists. For `podman` the context pin is not applied, and Podman connection settings in the user's own configuration are not checked.
- The test runner checks command exit status, container restrictions and unchanged source diff. It does not prove the test suite fully covers requirements or that code is free from vulnerabilities.
- Synchronized profiles, roles, and user identities do not imply multi-tenant authorization in this alpha.
- No recursive worker spawning, ambiguous-operation replay, direct host shell execution, commit/push/merge, or automatic deployment is permitted.  adds bounded new attempts only after independently completed failed checks; [repair recovery](repair-loops.md) rejects unresolved in-flight operations.

## Local smoke and test suites

```bash
python -m pytest -q tests/unit/test_code_provider.py tests/security/test_code_patch_security.py \
  tests/security/test_code_sandbox.py tests/integration/test_code_workflow.py \
  tests/integration/test_code_artifacts.py tests/contract/test_code_extension_contracts.py
python examples/mock_patch_agent.py
```

To run the optional real Docker check with a pre-pulled local image:

```bash
export DBOUND_DOCKER_TEST_IMAGE=python:3.12-slim
python -m pytest -q tests/integration/test_docker_real_optional.py tests/integration/test_docker_isolation_real.py
```

The second file probes the container from the inside: loopback-only network, read-only root, dropped capabilities, no new privileges, no host secrets or Docker socket, no disk-backed host path mounted except the workspace, CPU, pids and memory limits, and removal of a timed-out container. It runs in the `docker-code-sandbox` CI job with no skips allowed. The container runs as the calling user. A root caller is refused before Docker or Podman is contacted, so run the sandbox as a non-root user.

See [Coding agent execution](coding-agents.md) and [Verification](verification.md).
