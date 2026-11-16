# Database integrity, backups and offline recovery

DecisionBound's local runtime stores durable event receipts, project identity, approved decisions, and workflow metadata in SQLite. **The database is private user data**. Even when no API secrets are stored in SQLite, tasks and event metadata may be confidential. Backup files require the same access restrictions as the original database.

## Operational guarantees (v0.13 alpha)

- `dbound state check` opens an existing database in SQLite **read-only mode**; it does **not** create an empty missing database or silently migrate an unknown version.
- `dbound state check --full` runs SQLite `PRAGMA integrity_check` and a foreign-key check. Both modes enforce the known schema version, required core tables, and their required columns. The result contains no source code, project names, tokens or prompts.
- `dbound state backup --output …` uses SQLite's official **online backup API**. A consistent committed snapshot is created and integrity-checked while ordinary WAL readers/writers may continue.
- Backups are first written to a private temporary file in the destination directory, flushed, SHA-256 hashed, and published **without overwriting** an existing file. An interrupted or unsuccessful operation cleans up temporary files whenever the process is still alive.
- `dbound state restore` **never modifies a running database**. It copies a validated backup to a **new, separate destination file** and will refuse an existing file or the current active state path.
- Backup and restore do not contact models, cloud servers, external storage providers, or automatically synchronize devices.

## First: inspect your existing local state

```bash
dbound state check
dbound state check --full
```

For an explicit offline file:

```bash
dbound state check --database /private/backups/decisionbound-state.db --full
```

An invalid/corrupt/unsupported database produces a nonzero exit status. Do not respond by deleting the database or enabling automatic reinitialization.

## Take a consistent, private snapshot

```bash
mkdir -p "$HOME/private-dbound-backups"
chmod 700 "$HOME/private-dbound-backups" # POSIX; use your Windows account's protected folder on Windows

dbound state backup --output "$HOME/private-dbound-backups/state-2026-11-14.db"
```

The command prints JSON with the location, SHA-256, bytes and schema version, *not* database contents. Store the digest in your private backup records. Reusing the same output path is intentionally refused. Keep backup files **outside any Git repository**, restrict filesystem access, and encrypt them before transferring to external storage. Backup files may contain proprietary task descriptions or project metadata.

## Recover after an incident

Stop any live `dbound serve` processes before **manually** switching the active database. The release only provides a safe recovery-to-new-file command:

```bash
dbound state restore \
  --backup "$HOME/private-dbound-backups/state-2026-11-14.db" \
  --destination "$HOME/private-dbound-backups/recovered-for-inspection.db"

dbound state check --database "$HOME/private-dbound-backups/recovered-for-inspection.db" --full
```

For v1 → v2 upgrades, use the explicit [schema-upgrade procedure](schema-upgrades.md) to create a verified **new** database. Only after independent review and stopping existing processes may an operator activate recovered or upgraded state. There is no automatic in-place replacement command in this alpha: mixing a replacement file with an active WAL runtime or competing processes could lose committed work. Restoration does not rewrite audit history, declare task completion, or bypass approvals.

## Failure model and limitations

| Scenario | Expected behavior | What remains out of scope |
|---|---|---|
| Backup while SQLite writers are active | A consistent point-in-time committed snapshot | Distributed snapshot across other services or files |
| Existing destination or symlink target | Refuse overwrite | Arbitrary hostile kernel or same-user filesystem adversary |
| Corrupt source, mismatched schema | Fail without publishing a new backup | Forensic recovery of corrupted databases |
| Process failure before publication | Old state untouched; incomplete temp may remain after sudden power loss | Automatic debris cleanup after OS crash |
| Disk-full or I/O error | Stop; report error and preserve source | Guaranteed power-loss durability on every filesystem |
| Read-only recovery operation | New file, no modification to active database | Hot failover, auto-migration, or remote-device sync |

**Scale boundaries:** local SQLite is a valid per-device default, not a multi-tenant distributed database. The platform still requires explicit consistency models, authentication, tenant isolation, observability, and independently tested PostgreSQL service architecture before remote fleet use. These commands do not establish those properties.

## Tests and operating evidence

- `tests/integration/test_state_integrity_sprint12.py` covers corruption, schema mismatch, concurrent writer snapshots, parallel backup races, symlink targets, restoration refusal, cleanup after injected failure, and credential-safe CLI output.
- `scripts/certify_platform.py --http-node` now also validates the complete backup → SHA-256 → inspect → recover cycle using a built and **installed wheel** on each supported CI OS.
- The Windows, macOS and Linux hosted matrix is defined in `.github/workflows/platform-certification.yml`; if a hosted run has not executed, that OS is **not certified**.

[Host certification](host-certification.md) · [Security boundaries](security.md) · [Python API reference](api-reference.md)

## Bounded local write/backup stress probe

To measure event persistence, event-order preservation and online backup integrity under concurrent *local threads*:

```bash
PYTHONPATH=src python scripts/stress_local_state.py --writers 8 --events-per-writer 250 --backups 4
```

The script uses a disposable directory, emits aggregate elapsed time and empirical p95/p99 write durations, and fails if event counts, ordering or snapshots are inconsistent. Its metrics are **observations for the host that ran the test**, not claims about thousands of distributed installations, cloud latency, or production throughput. Do not extrapolate the results across operating systems or machines without executing the actual hosted-runner matrix and collecting repeated measurements.
