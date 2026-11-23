# Human patch review & export approvals

> ** · experimental alpha.** This is a **local patch-export approval gate**. It does not authorize writing to your source tree, merging changes, committing, pushing, deploying or certifying semantic task completion.

DecisionBound separates three independent assertions:

1. **Artifact integrity:** the reviewed patch bytes match the host-side SHA-256 receipt, the coding attempt, approved contract, base commit and workflow project.
2. **Contract verification:** only a real isolated test runner with consistent host receipts may establish that *specified tests* passed. Mocked tests remain explicitly unverified.
3. **Local export approval:** a local operator consciously accepts the *exact* patch snapshot, with a separate acknowledgment for unverified tests. This only permits saving a patch file outside the repository.

## End-to-end user flow

The reviewer first runs a supervised coding workflow (`dbound workflow supervised-code`), then inspects the result:

```bash
# Review source-free metadata. No code text is printed.
dbound review inspect wf_0123456789abcdef0123456789abcdef

# To consciously inspect source code, opt in to diff output.
dbound review inspect wf_0123456789abcdef0123456789abcdef --show-diff

# Use the exact fingerprint from inspection. For a mock-tested candidate:
dbound review approve wf_0123456789abcdef0123456789abcdef \
  --fingerprint <64-hex-snapshot-hash> --revision 0 \
  --acknowledge-unverified --confirm-export-approval

# The output must be OUTSIDE the source repository.
dbound review export wf_0123456789abcdef0123456789abcdef \
  --fingerprint <same-hash> --revision 1 --output /tmp/candidate.patch

# Change your mind (no new exports allowed with this approval):
dbound review revoke wf_0123456789abcdef0123456789abcdef \
  --fingerprint <same-hash> --revision 1

dbound review history wf_0123456789abcdef0123456789abcdef
```

Export creates a new file exclusively (`O_EXCL`, mode `0600` where supported), refuses existing destinations and checks the artifact digest again. The user still decides whether and how to apply the exported patch using their own Git process. **No command auto-merges.**

### Browser control room

Run the authenticated local API and open the loopback-only control room. Supply the locally provisioned bearer pairing token, then a workflow ID. The **Patch review & export approval** panel supports:

- Metadata-only inspection and an explicit **Preview patch** action (raw diff not requested otherwise).
- Warnings when acceptance tests are mocked, incomplete or otherwise unverified.
- A positive confirmation checkbox and an independent unverified-test acknowledgment.
- Revision-checked approve/reject/revoke decisions using the same local database as the CLI.
- Source code rendered as text, never injected as HTML. No browser endpoint exports, applies, merges, pushes or runs coding workers.

Only `localhost`/loopback Host and Origin values are accepted; all API operations require the local bearer token. This is **local single-user pairing**, not enterprise identity or remote multi-tenant authorization. A browser connected to the local user has the privileges of that local runtime. Never expose the service on an external network interface.

### CLI, MCP, SDK

- CLI, local web UI, and Python SDK use the same `ReviewGate` and local SQLite ledger.
- MCP offers **`get_workflow_review_metadata`** for the fixed, trusted project only; there is no raw diff, approval, export, or code modification MCP tool.
- `ReviewGate.snapshot(..., include_diff=False)` deliberately omits source. `include_diff=True` is an explicit local-user request.
- `ReviewGate.decide` uses `expected_revision` and an SHA-256 snapshot fingerprint; conflicting clients fail closed.
- `ReviewGate.export` rechecks evidence and acquires the local SQLite writer lock when checking approval and creating the file, so concurrent revocation cannot interleave mid-export.
- `ReviewGate.history` records metadata-only approve/reject/revoke/export events, not prompts, source, or private test output.

## Stale evidence and safety

A review fingerprint incorporates the workflow and project IDs, coding run, patch digest, contract digest, base commit and review state. A source commit change, unclean checkout, modified/symlinked patch, altered receipt or revoked approval blocks export. A rejected fingerprint cannot be re-approved without new evidence; attempts to repeat stale approvals fail through optimistic revision control.

If Git cannot answer while the gate compares the source commit and the tracked file modes (Git missing, a timeout, an error exit or an unreadable repository), `snapshot`, `decide` and `export` raise `ReviewGitUnavailable` and nothing is approved or exported. The local API returns HTTP 409 with the fixed message `Git state could not be verified, so the review was refused`, and MCP raises the same message. It never contains local paths, Git error text or environment values. A checkout that Git reports as modified keeps its own refusal message.

A workflow with no finished/cleaned coding attempt, ambiguous receipts, or an interrupted write is not reviewable. Revocation does not remove an already exported file: it prohibits subsequent authorized exports. The historical audit ledger remains distinct from the current filesystem attestation.

**Limitations:** This gate does not cryptographically authenticate a human operator, prevent a malicious local process with equivalent OS permissions from altering state, guarantee a filesystem snapshot remains static for all time, or prove a bug is fixed. Real Docker/MCP/proprietary-provider interoperability must still be tested on a connected host. Production use requires additional threat modeling, operational controls, and independent validation.

## Extensibility

Subclass `ExportReviewPolicy` for *additional vetoes* and inject it into `ReviewGate`. A custom policy can **never** bypass canonical integrity checks, permission/consent controls, or the approval ledger. Both `snapshot` and `export` always execute host checks; protected credentials, raw test stdout and model messages are not part of the review event history. See [API reference](api-reference.md) and [Security](security.md).
