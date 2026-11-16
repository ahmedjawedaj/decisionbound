# End-user setup, readiness and first-run safety

DecisionBound's setup flow is for **people installing the product**, not for the engineering team that builds DecisionBound. A new user can choose a profile, initialize their repository explicitly, inspect missing prerequisites, and start an offline inspection without an LLM API key. Profiles are user-level preferences; they do **not** authorize external requests or code writes.

## The short path: install → choose → inspect → start

Install from the source release or a built Python package:

```bash
python -m pip install -e '.[web]'
cd /path/to/your/repository
# One explicit project registration and profile selection:
dbound setup --profile local-safe --path . --initialize
# No remote account or provider key required:
dbound doctor --path . --strict
dbound prepare 'Explain this repository architecture' --path .
```

The `setup` command prints stable JSON. It never sends your repository content to a model, prompts for a secret, launches a model process, or grants network/write privileges. If a project manifest already exists, it must match an identity in the local registry: **a copied, unknown or corrupt manifest is not silently registered**. Without `--initialize`, setup does not create a project manifest.

### Choose a provider later

```bash
# See templates and current choice
dbound profile list
# Noninteractive (automatable)
dbound setup --profile claude
# Optional interactive profile selection, no secret prompt
dbound setup --interactive
# Report missing prerequisites and return exit code 2 if required ones are absent
dbound doctor --profile openrouter --strict
```

Available presets are `local-safe`, `local` (Ollama), `claude`, `codex`, `qwen`, `openrouter`, and `litellm`, plus explicitly imported custom profiles. Use `dbound profile create TEAM --from PRESET` to create a shareable overlay. A custom profile's YAML cannot store API keys or arbitrary nested policy settings.

## What readiness actually means

The `ready` field means only that **locally inspectable prerequisites** are present. It never means an external provider authenticated successfully, that a model is downloaded, that a Docker container is secure, that MCP wire compatibility passed, or that a coding task is completed. The `integration_tested` field is currently always `false` for setup/doctor results.

| Choice | Required local prerequisite | Never inferred |
|---|---|---|
| `local-safe` | None | Network, model calls, write consent |
| `local` | `ollama` executable | Local model availability or service health |
| `claude` | `claude` executable | CLI logged in, API validity, vendor compatibility |
| `codex` | `codex` executable | CLI logged in, API validity, vendor compatibility |
| `qwen` | `QWEN_API_KEY` presence | Valid key, network consent |
| `openrouter` | `OPENROUTER_API_KEY` presence | Valid key, model permissions |
| `litellm` | `LITELLM_API_KEY` presence | Gateway availability, route selection |

For external model calls, use an **explicitly authorized** `dbound agent run` or another supported execution command with its own budget/permission controls. Neither selecting a profile nor setting an environment variable authorizes a paid call. For code modification, the isolated worktree/write and sandbox flags are separately required. An exported patch is not automatically merged.

## What can be shared safely

`dbound profile export` writes only allowlisted profile fields. Authentication is resolved separately from exports. Recommended practice is to supply credentials through your OS secret manager or a controlled environment when invoking an actual provider-backed command, never via `.decisionbound/project.yaml`, repository `.env` files or VS Code settings.

Never paste a pairing token into a README or public issue. The local runtime's `dbound client pair --show-token` is an intentional secret disclosure and should only be used in a private terminal. VS Code stores it in SecretStorage; the browser holds it only in a password input for the current page. An ID such as `prj_*` scopes local queries but is **not an authentication credential**.

## One state across CLI, browser and editor

The CLI uses `SetupService.inspect()` and `SetupService.configure()`. The authenticated local web API exposes `GET /api/setup/v1/status`; the page's **Profile setup and readiness** panel loads profiles, switches the active preset, and shows missing prerequisites. The VS Code command **DecisionBound: Check Local Setup** uses the same read-only HTTP status with a pairing token. All clients share one SQLite state store.

The HTTP endpoint requires `Authorization: Bearer <local token>` and intentionally does **not** accept arbitrary filesystem paths. It returns only the selected profile, prerequisite booleans, environment-variable **names**, and next actions. It does not return environment values, API keys, raw tasks, source code, or model messages. Browser output uses `textContent` to prevent rendering untrusted strings as HTML.

## Extending setup safely

`decisionbound.onboarding.SetupService` accepts a SQLite state store, an injectable environment mapping and executable resolver (`which`) for tests or custom infrastructure. Its `inspect` method is side-effect-free at the service layer; the CLI may create the base local SQLite state on first launch. Its `configure` method is intentionally restrictive. Replaceable UI flows must continue to enforce the following invariants:

- Resolve and validate a profile **before** mutating project identity or active selection.
- A known, previously registered project manifest must verify; do not trust copied IDs.
- Reveal **only** presence of secrets, not their values, lengths, prefixes or paths.
- Treat readiness as prerequisites, never as proof of successful authentication or testing.
- Keep network/model and code-write consent as separate host-enforced controls.
- Do not use profile YAML to override repository policy, verification or approval gates.

The generated [API reference](api-reference.md) includes `SetupService`, `SetupReport` and `SetupCheck` docstrings. Extension transport overrides should preserve strict loopback URL validation, redirect refusal and SecretStorage-based token handling.

## Tests and release gates

The local test suite includes invalid profiles, symlinked/corrupt/foreign project manifests, missing executables, credential redaction, CLI strict exit codes, authenticated API behavior, mock-host VS Code behavior, a real Node HTTP client talking to Uvicorn, and a Chromium DOM injection check with mocked HTTP.

**Still requires a connected environment:** true Chromium-to-Uvicorn navigation, the real VS Code Extension Development Host, installable VSIX packaging, real Docker sandbox execution, official MCP SDK v2 conformance, and installed Claude/Codex integrations. These are separate CI jobs. The sandbox used to produce this alpha blocks browser localhost navigation, so no browser-to-server success is claimed locally.

See [Profiles](profiles.md), [Local clients & VS Code](local-clients.md), [Security](security.md) and [Roadmap](roadmap.md).
