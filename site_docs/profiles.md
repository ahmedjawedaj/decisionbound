# End-user profiles and onboarding

Profiles are first-class end-user configuration. A developer starts with `local-safe` (no network) and can switch to a named provider/harness template. **A preset does not imply a working Claude Code or Codex SDK integration.**

## Built-in choices

| Profile | Provider | Harness target | Calls enabled by default? |
| --- | --- | --- | --- |
| `local-safe` | None | Offline | No |
| `local` | Ollama | Read-only adapter (future) | No |
| `claude` | Anthropic | External Claude adapter (future) | No |
| `codex` | OpenAI | External Codex adapter (future) | No |
| `qwen` | Configurable | Read-only adapter (future) | No |
| `openrouter` | OpenRouter | Read-only adapter (future) | No |
| `litellm` | LiteLLM gateway | Read-only adapter (future) | No |

## First-run setup and diagnostics

```bash
dbound setup --profile local-safe --path . --initialize
dbound setup --interactive
dbound doctor --profile claude --strict
```

Diagnostics check only prerequisites; they do not perform model calls, validate paid credentials, or enable writes. The browser setup panel and VS Code **Check Local Setup** command read the same authenticated state. Full procedure: [Setup & diagnostics](setup-diagnostics.md).

## CLI examples

```bash
dbound profile list
dbound profile use local-safe
dbound profile create backend-team --from codex
dbound profile use backend-team
dbound profile export backend-team --output backend-team.yaml
dbound profile import reviewed-team.yaml
```

A custom profile can override `provider`, `harness`, `model`, `network`, `context_max_tokens`, `max_steps`, and `max_workers`. Only a **single built-in** parent is permitted initially. Profile revisions are checked during updates via the Python API; fine-grained editing CLI commands are not yet implemented.

## Shared configuration, separate secrets

User-specific active profile state belongs to the local runtime, not to each client. It is shared across CLI and web API. Exported YAML includes only an allowlisted set of safe configuration keys; never put API keys in profile files.

- Do not copy your provider token into `.decisionbound/project.yaml` or any shareable YAML file.
- A profile's `network: true` flag alone never grants network access; that requires an authorized adapter and runtime permissions.
- Switching profiles does not modify an active run's historical event record. Future executions should eventually pin the fully resolved effective profile snapshot.
- Team sync and remote identity are not implemented in this alpha.
