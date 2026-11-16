# Schema upgrades without silent data changes

DecisionBound's local SQLite state contains review decisions, project identity,
workflow journals, cost reservations, and metadata-only event traces. Any
migration must preserve history, prevent fabricated timestamps, and keep the
original database available for rollback.

## Current contract (v0.13 alpha)

- The current core schema is **2**; it is independent of the package version.
- Opening an existing **v1** database no longer silently upgrades it. The
  ordinary runtime fails with a migration instruction before changing SQLite's
  journal mode or creating tables.
- Unversioned databases that contain user tables and unknown future schemas
  are rejected. No auto-adoption of arbitrary SQLite files.
- `dbound state migrate` snapshots an existing v1 (or current v2) SQLite file
  with SQLite's online backup API, migrates **only the private copy**, runs
  complete integrity and foreign-key checks, and publishes a new path with
  atomic no-overwrite semantics.
- Missing legacy `run_events.created_at` values become the explicit sentinel
  `unknown`, not synthetic creation timestamps.
- Health checks verify required columns, not just table names.

## Safe operator workflow

Stop existing DecisionBound processes before activating any migrated state:

```bash
# Review health, keep the old database, choose an unused destination.
dbound state migrate \
  --source "$HOME/.decisionbound/state.sqlite3" \
  --output "$HOME/.decisionbound/state-migrated.sqlite3"

# Inspect new state independently; do not overwrite the original.
python - <<'PY'
from decisionbound.state_integrity import inspect_database
from pathlib import Path
p = Path.home()/'.decisionbound'/'state-migrated.sqlite3'
print(inspect_database(p, full=True).to_dict())
PY
```

Review the new file, stop all old runtimes, then activate the replacement through
an explicitly reviewed operating-system procedure. DecisionBound does **not**
automatically switch the active path, merge states, or delete the original. A
rollback is simply returning to the old path with *all writers stopped*; it is
not a bidirectional data merge. Any writes made after switching to a migrated
copy will not magically appear in the original.

## Limitations

This is **single-user, single-device** migration preparation, not remote fleet
rollout, dual-write synchronization, or online multi-tenant schema migration.
For thousands-device distribution, maintain an upgrade coordinator, signed
artifacts, rollback-compatible client versions, restart fencing, and telemetry
on migration failures. Cross-platform CI is configured but Windows/macOS hosted
results remain pending until published.
