# Quickstart: from installation to a verified decision

This page was run from a clean install. The demo below is executed by the test suite (`tests/integration/test_docs_quickstart.py`) from the installed package, and its output is compared with the blocks shown here.

## Install

Python 3.11 or newer and Git. The default mode needs no cloud account, API key or network access. DecisionBound is installed from source because there is no PyPI package.

```bash
git clone https://github.com/ahmedjawedaj/decisionbound.git
cd decisionbound
python -m venv .venv && source .venv/bin/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
dbound --help
```

Use `'.[web]'` instead of `.` if you want the local web interface (`dbound serve`). Contributors use `python -m pip install -e '.[test,web,docs,release,mcp]'`.

## 60-second demo

Run it in bash or zsh from an empty directory outside the clone, for example after `cd ..` (on Windows use Git Bash or WSL). `DBOUND_HOME` keeps all state in a throwaway folder, so your real `~/.decisionbound` is untouched.

<!-- quickstart:begin -->
```bash
mkdir dbound-demo && cd dbound-demo
export DBOUND_HOME="$PWD/state"   # throwaway state, nothing is written to ~/.decisionbound
mkdir -p shop/docs/adr blog acme

cat > shop/docs/adr/0001-thin-handlers.md <<'EOF'
# Thin handlers

## Context
HTTP code that held business rules was hard to test.

## Decision
Keep request handlers thin and put business rules in a separate module.
EOF

# 1. Record a decision with its evidence, then approve it as a human reviewer.
dbound init --path shop --name shop --owner ahmad --technologies python,django
dbound decision discover docs/adr/0001-thin-handlers.md --path shop --scope personal
ID=$(dbound decision list --path shop | python -c 'import json,sys; print(json.load(sys.stdin)[0]["decision_id"])')
dbound decision approve "$ID" --revision 1 --path shop

# 2. Ask what each repository may do with it.
dbound transfer --path shop                  # same project: apply
dbound init --path blog --name blog --owner ahmad --technologies python,flask
dbound transfer --path blog                  # same owner, compatible: translate
dbound init --path acme --name acme --owner someone-else --technologies python
dbound transfer --path acme                  # different owner: abstain

# 3. Compile the budgeted context a task would receive.
dbound prepare "Add a comments endpoint" --path blog --tokens 1200
```

Each `transfer` prints what that repository may do with the approved decision (identifiers differ on every run, and `sha256` is the hash of the ADR file above):

`shop`, the project the decision came from:

```json
[
  {
    "action": "apply",
    "decision_id": "dec_8727fde99ffe4d6fb90676790c251c41",
    "evidence_sha256": "d39cab7769e0d10b615f997e4ebf82c3c911fb89b74bbcf4f2d247fe3cdbc518",
    "reason": "Approved decision from the same project"
  }
]
```

`blog`, a different project with the same owner and compatible technology:

```json
[
  {
    "action": "translate",
    "decision_id": "dec_8727fde99ffe4d6fb90676790c251c41",
    "evidence_sha256": "d39cab7769e0d10b615f997e4ebf82c3c911fb89b74bbcf4f2d247fe3cdbc518",
    "reason": "Personal principle is compatible but needs target-repository adaptation"
  }
]
```

`acme`, a project with a different owner:

```json
[
  {
    "action": "abstain",
    "decision_id": "dec_8727fde99ffe4d6fb90676790c251c41",
    "evidence_sha256": "d39cab7769e0d10b615f997e4ebf82c3c911fb89b74bbcf4f2d247fe3cdbc518",
    "reason": "Personal decision owner differs from target owner"
  }
]
```

`prepare` then compiles the context a task in `blog` would receive, with the evidence hash attached:

```json
{
  "estimated_tokens": 24,
  "items": [
    {
      "key": "dec_8727fde99ffe4d6fb90676790c251c41",
      "priority": 10,
      "protected": false,
      "source": "docs/adr/0001-thin-handlers.md#sha256=d39cab7769e0d10b615f997e4ebf82c3c911fb89b74bbcf4f2d247fe3cdbc518",
      "text": "Keep request handlers thin and put business rules in a separate module."
    }
  ],
  "max_tokens": 1200,
  "omitted_keys": [],
  "task": "Add a comments endpoint"
}
```
<!-- quickstart:end -->

What it shows: a decision is a source file plus a human approval, and reuse in another repository is an explicit outcome. `apply` is the same project, `translate` is compatible but needs adapting by a person, `abstain` means the policy refuses, and `escalate` (not shown) means the target owner forbids it. Notes: `approve --revision 1` returns revision `2`, because discovery records a candidate and approval records the next revision. The decision is declared `--scope personal` so that it can leave its own project, and a project-scoped decision never transfers.

## Use it on your own repository

Select a profile and register the repository explicitly. Neither command needs credentials:

```bash
cd /path/to/your/project
dbound setup --profile local-safe --path . --initialize
dbound doctor --path . --strict
dbound status --path .
```

`doctor` prints `"write_consent": false` as an informational line (code writes always need separate consent) and `"ready": true` when the project is registered. `--strict` exits nonzero when a required check fails. `setup --initialize` registers the project with the owner `local` and no technology tags. To set an owner and tags, use `dbound init --name my-project --owner me --technologies python,django` instead, because transfer decisions depend on them.

State is stored under `~/.decisionbound/` by default, shared by all your projects. Set `DBOUND_HOME` to use another folder.

## Create and review evidence

Add an architecture record such as `docs/adr/0001-domain.md`. Discovery requires nonempty `## Context` and `## Decision` sections:

```markdown
# Domain boundaries

## Context
Keep HTTP transport separate from domain decisions for testability.

## Decision
Keep handlers thin; put nontrivial business rules in a dedicated module.
```

Inspect the candidate and approve it only after reviewing its source. Use the decision ID and revision from the JSON output:

```bash
dbound decision discover docs/adr/0001-domain.md
dbound decision list
dbound decision approve DECISION_ID --revision 1
```

If the evidence file changes after discovery, approval fails and you must rediscover and review it.

## Prepare and inspect a task

```bash
dbound prepare "Add a read-only API endpoint" --tokens 1000
dbound transfer
dbound run "Add a read-only API endpoint"
dbound trace RUN_ID
```

`prepare` returns the budgeted context capsule. `run` performs an offline preparation step and does not change source code, and its result states `task_completed: false` even when preparation is verified. `RUN_ID` is the `run_id` printed by `run`.

For a deterministic read-only tool run with no model, `dbound agent demo "handler" --path .` searches your files for that text. The search is a plain substring match, so a term that is not in your files returns an empty result.

## Local web interface

Needs the web extra (`python -m pip install '.[web]'` from the clone).

```bash
dbound serve
```

Open `http://127.0.0.1:8765`. The pairing token is stored in `local_token` inside the state folder (`~/.decisionbound/local_token` by default). It is a credential, so do not paste it into issues or commit it. The server only binds to loopback.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `dbound: command not found` | Activate the virtual environment where you ran `pip install`. |
| Project not initialized | Run `dbound init` or `dbound setup --initialize` in the repository, or pass `--path`. |
| `Decision does not belong to this project` | Run `decision approve` with the `--path` of the project that discovered it. |
| Manifest conflicts with registry | Restore the authorized manifest. Do not copy project IDs between unrelated projects. |
| ADR discovery refuses a document | Include nonempty `## Context` and `## Decision` sections. |
| Source changed before approval | Rediscover and review the new evidence. |
| `prepare` returns no items | No approved decision applies to this project. Approve one, and check `dbound transfer`. |
| Protected context does not fit | Increase `--tokens` or simplify the task. Protected items are never silently dropped. |
| Missing web dependencies | Install `'.[web]'`. |

Next: [Profiles](profiles.md), [Python SDK](sdk.md), [CLI reference](cli.md) and [Security](security.md).
