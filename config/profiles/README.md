# Built-in profile templates (proposal, not working runtime presets)

Profiles consist of **connection/harness templates** plus an **execution policy** and optionally an **approved taste profile**. These YAML documents are safe, credentials-free reference artifacts. No exact vendor model IDs are assumed; the first-run wizard must check provider availability and authorized exact model names.

Do not conflate the `codex` harness with any OpenAI API model, or Claude Code with an Anthropic model endpoint. Avoid storing tokens in profile YAML. `credential_ref` is an environment/secret-manager **reference**, not a secret.

| ID | Intended usage | Cost on first run |
|---|---|---|
| `local-safe` | Offline, read-only, no external tools | $0 |
| `claude` | Anthropic provider or installed Claude harness | Requires explicit provider credentials / authorization |
| `codex` | Installed Codex SDK/CLI integration (separate from model) | Subject to installed harness auth |
| `qwen` | Qwen through configured compatible endpoint | User-specified |
| `openrouter` | Multi-provider OpenRouter connection | User-specified |
| `litellm` | Existing team LiteLLM gateway | User-specified |
| `local` | Local Ollama/compatible endpoint | No API key required in common local setups |

Any executable profile requires a verified provider/harness adapter, matched capability contract and enforceable budget policy. Files here are configuration design examples, not validated real provider connections.
