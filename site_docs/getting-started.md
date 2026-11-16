# Quickstart — from installation to verified evidence

## Prerequisites

Python 3.11 or newer. The default local mode needs neither a cloud account nor an LLM API key. SQLite is part of Python. The project uses the Typer and PyYAML packages; the optional local API uses FastAPI and Uvicorn.

```bash
git clone <your-repository-url>
cd decisionbound
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install -e '.[test,web,docs]'
```

For the shortest end-user setup path, select a safe default and explicitly register the repository:

```bash
cd /path/to/your/project
dbound setup --profile local-safe --path . --initialize
dbound doctor --path . --strict
```

This is local and requires no model credentials. For provider-specific setup, use `dbound setup --interactive` followed by `dbound doctor --strict`. See [Setup & diagnostics](setup-diagnostics.md).

Create a fresh project in a **separate** workspace, or use an existing repository:

```bash
cd /path/to/your/project
dbound init --name sample-project --owner local --technologies python
```

The initializer creates `.decisionbound/project.yaml`; the user-wide registry is stored under `~/.decisionbound/state.sqlite3` by default. Project IDs identify resources but do not authorize access.

## Create and review evidence

Add an architecture record `docs/adr/0001-domain.md`:

```markdown
# Domain boundaries

## Context
Keep HTTP transport separate from domain decisions for testability.

## Decision
Keep handlers thin; put nontrivial business rules in a dedicated module.
```

Then inspect the candidate and approve **only after reviewing its source**:

```bash
dbound decision discover docs/adr/0001-domain.md
dbound decision list
# Use the actual ID and revision in the JSON output:
dbound decision approve dec_... --revision 1
```

If the evidence file changes after discovery, approval fails. The content hash must be reconsidered; this is not an automatic promotion process.

## Prepare and inspect a task

```bash
dbound prepare "Add a read-only API endpoint" --tokens 1000
dbound transfer
dbound run "Add a read-only API endpoint"
dbound trace run_...
```

`run` performs an **offline preparation step**, not source-code modification. Its result explicitly states `task_completed: false` even when preparation passes verification.

## Shared local API

```bash
dbound serve
# in another terminal: retrieve the local_token from ~/.decisionbound/local_token
```

Open `http://127.0.0.1:8765`; the local token is required to inspect profiles. All API endpoints run on loopback, and a remote bind is refused.

## Common troubleshooting

| Symptom | Resolution |
| --- | --- |
| Project not initialized | Run `dbound init` in the desired workspace. |
| Manifest conflicts with registry | Restore the authorized manifest; do not copy project IDs between unrelated projects. |
| ADR discovery refuses a document | Include nonempty `## Context` and `## Decision` sections. |
| Source changed before approval | Rediscover and review the new evidence. |
| Protected context does not fit | Increase `--tokens` or simplify task, never silently discard the requirement. |
| Missing web dependencies | Install `.[web]`. |

Next: [Profiles](profiles.md), [Python SDK](sdk.md), and [Security](security.md).
