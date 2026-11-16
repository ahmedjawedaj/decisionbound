# Offline signed-release verification

The v0.13 research alpha now contains a **real Ed25519 release-attestation
verifier**. It checks a detached signature against an independently provisioned
trusted public key, rejects stale/downgrade metadata, then streams SHA-256
verification over each listed local artifact. It does not install anything.

## Why checksums inside a ZIP are insufficient

An attacker who can replace both a ZIP and its internal manifest can recalculate
all hashes. SHA-256 manifests detect accidental corruption, **not authenticated
publisher identity**. A detached Ed25519 signature protects canonical release
metadata only if a trustworthy public key is provisioned *out of band*.

## Trusted manifest format

```json
{
  "format": "decisionbound-signed-release-v1",
  "channel": "alpha",
  "version": "0.13.0a1",
  "issued_at": "2026-11-14T00:00:00Z",
  "expires_at": "2026-12-14T00:00:00Z",
  "artifacts": [
    {
      "name": "DecisionBound-v0.13.0a1-Full-Source.zip",
      "sha256": "<64 lowercase hexadecimal characters>",
      "size_bytes": 123456
    }
  ]
}
```

The example is a schema illustration only; it is not a signed real release.
The exact manifest bytes must equal sorted-key, compact JSON UTF-8 plus a
single trailing newline. The detached signature is Base64-encoded Ed25519 over
those exact bytes. Duplicate JSON keys, unknown fields, local timestamps,
missing files and unsafe names are rejected.

## Verification command

```bash
pip install 'decisionbound[release]'

dbound release verify \
  --manifest ./release.json \
  --signature ./release.sig \
  --trusted-public-key /etc/decisionbound/trusted-release-publisher.pem \
  --artifacts-dir ./downloaded \
  --installed-version 0.12.0a1 \
  --minimum-accepted-version 0.12.0a1
```

**Never download `trusted-release-publisher.pem` from the same untrusted
manifest URL.** Provision it through independent, authenticated organizational
policy or an offline verified channel. The CLI does not accept a `--trust-any`
option, silently import keys, or create a production signing key.

An additional persistent anti-rollback floor is optional and should be stored in
protected device policy. A correct signature cannot by itself prevent replay
of a previously valid old release. Expiry depends on a trustworthy UTC clock.
No OS-level secure clock, key rotation ceremony, certificate transparency,
TUF-style repository delegation, or automatic rollout has been implemented.

## Release engineering

`python scripts/release_roundtrip.py` builds the source and GitHub Pages ZIPs,
checks for unsafe entries and incorrect manifests, and confirms byte-for-byte
reproducibility from unchanged source. It cannot authenticate its own publisher.
The independent verifier, unit tests with ephemeral private keys and the
cross-platform CI jobs are required before any release candidate is considered
for external distribution.

Do **not** call this module an updater: there is no remote download, unattended
code execution, migration activation, device enrollment, rollout percentage,
rollback orchestration or fleet telemetry. Those remain separate production
gates, particularly for thousands-device deployment.

See [Production readiness](production-readiness.md), [State upgrades](schema-upgrades.md),
and [Security](security.md).
