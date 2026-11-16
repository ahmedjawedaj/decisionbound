# Real installation and host certification

DecisionBound can be tested as **an installed product**, not merely imported from the source repository. The independent script `scripts/certify_platform.py` creates a fresh Git repository and private DecisionBound state directory, launches the installed console entrypoint, and checks actual CLI behavior. The optional `--http-node` flag starts a real Uvicorn process and connects the extension's Node transport without mocking HTTP.

## What was exercised on Debian Linux

- Installed Python wheel from outside the source directory; Python 3.13, Git and Node 22.
- `dbound doctor --strict` fails before explicit project registration; `dbound setup --profile local-safe --initialize` creates a private, registered project; the same strict diagnostics then pass.
- `dbound prepare`, offline `dbound run`, and bounded mock `dbound agent demo` run without credentials; they do **not** claim task completion.
- Custom profile creation and selection work; a durable workflow appears in the CLI sessions list; the original README remains unchanged.
- The real Uvicorn API refuses an unauthenticated request. The real VS Code Node transport reads project-scoped sessions, workflow status, and events using the pairing token and preserves `task_completed=false`.
- Pairing credentials are never written to stdout or the test report. State and fixture data are discarded when the test exits.

The environment did not provide a hypervisor (`/dev/kvm`), Docker, installed VS Code, or working DNS for the official VS Code download. The wheel's Python dependencies were preinstalled and reused during local certification. **This is not a fresh dependency-resolution or VS Code host pass.**

## Run the same certification on Linux, Windows or macOS

From the extracted release directory, with Python 3.11+ and Git installed:

```bash
python -m pip install 'typer>=0.9' 'PyYAML>=6' 'fastapi>=0.100' 'uvicorn>=0.20'
python -m pip wheel . --no-deps --wheel-dir dist
python -m pip install --no-index --no-deps dist/decisionbound-0.12.0a1-py3-none-any.whl
python scripts/certify_platform.py
# Requires Node.js 22+; exercises the real local API and extension client:
python scripts/certify_platform.py --http-node
```

The certification script uses Python's `subprocess`, `tempfile`, and `pathlib` rather than shell-specific commands; it works with Windows-style paths. Tests require explicit `DBOUND_HOME` state isolation and fail if `decisionbound` resolves to the checked-out `src` tree. A nonzero exit or missing check means **not certified**.

## Launch real hosted Windows/Linux VMs and VS Code

`.github/workflows/platform-certification.yml` defines a GitHub Actions matrix of Ubuntu, Windows and macOS on Python 3.11 and 3.13. Every job builds and installs the wheel, runs the real CLI setup and workflow smoke, then connects Node to Uvicorn. Independent Ubuntu and Windows jobs download VS Code through `@vscode/test-electron`, launch the Extension Development Host, exercise command registration and activation, and package a reviewable VSIX using `@vscode/vsce`.

These hosted jobs require the source to be pushed to a repository with Actions enabled. **The local project has no configured DecisionBound GitHub repository and none of the matrix jobs has been run yet.** Do not mistake a green local mock-host test for a real VS Code launch.

For manual VS Code testing, open an initialized fixture repository, run `dbound serve`, obtain the private pairing token through `dbound client pair --show-token`, and install the CI-produced VSIX through **Extensions → Install from VSIX**. Run **DecisionBound: Pair Local Runtime**, **Check Local Setup**, and **Refresh Sessions**; verify SecretStorage, workspace trust and event replay. Never commit the token or allow the extension to write patches.

## Required release evidence

| Certification | Local status | Required externally |
|---|---|---|
| Installed Linux CLI/setup/prepare | Passed with preinstalled dependencies | Fresh dependency install in CI |
| Real Node→Uvicorn→SQLite | Passed on Linux | Windows/macOS hosted runs |
| VS Code mock-host contract | Passed | Real Extension Development Host |
| VSIX official packaging | Not tested (tool unavailable) | `@vscode/vsce` CI output/install |
| Real Docker isolation | Not tested (no daemon) | Docker-enabled CI |
| MCP v2 official protocol | Not tested locally | SDK-enabled CI |
| Live Claude/Codex / model provider | Not tested | Operator-authenticated execution |
| Browser↔local API | Navigation blocked locally | Connected browser runner |

The platform is still an experimental single-user alpha. It cannot claim production certification, research improvements, or arbitrary task completion on the basis of smoke tests.

## Additional v0.12 installed-distribution check

The installed-wheel certification now runs a **tenth** check: SQLite health → online backup → SHA-256 comparison → read-only inspection → safe recovery to a new path. All operations use the console script from the installed wheel, not the checked-out package. The source database and original Git fixture remain unchanged. Passing this local check is not proof of cloud recovery or successful Windows/macOS hosted jobs.
