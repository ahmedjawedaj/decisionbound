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
- Docker rootless mode, seccomp policies, image signatures, CVE policies, resource governance and host kernel security are operator responsibilities. Image pull is disabled during execution; images should be pre-pulled and ideally digest-pinned.
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
python -m pytest -q tests/integration/test_docker_real_optional.py
```

See [Coding agent execution](coding-agents.md) and [Verification](verification.md).
