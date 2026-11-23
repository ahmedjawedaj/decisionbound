# MCP integration: read-only project intelligence

DecisionBound exposes a **project-bound** Model Context Protocol server using the [official MCP Python SDK v2](https://github.com/modelcontextprotocol/python-sdk) as an optional dependency.

## Install and run

```bash
python -m pip install 'decisionbound[mcp]'
# From an initialized repository
dbound mcp serve --path .
```

Use the command above in the MCP host's local stdio server configuration. The process writes MCP frames to stdout; do not surround it with a script that prints startup logs to stdout.

Example host configuration (format varies by product and version):

```json
{
  "mcpServers": {
    "decisionbound": {
      "command": "dbound",
      "args": ["mcp", "serve", "--path", "/absolute/path/to/initialized/repository"]
    }
  }
}
```

**Do not copy that JSON blindly into Codex or Claude without checking their currently supported configuration format.** It illustrates stdio arguments; native host integration remains to be tested.

## Exposed capabilities

| MCP tool/resource | Result | Permission |
|---|---|---|
| `project_info` | Name, logical ID, technologies | Read-only |
| `search_project(query)` | Capped lexical file matches | Read-only |
| `read_file(path)` | Capped safe-source excerpt | Read-only |
| `list_project_decisions` | Status and statement, no evidence file contents | Read-only |
| `preview_transfer` | Transfer classification / rationale | Read-only |
| `prepare_context(task,max_tokens)` | Evidence-grounded context capsule | Read-only |
| `get_workflow_status(workflow_id)` | Current stage statuses and source-free receipt hashes; same project only | Read-only |
| `get_workflow_events(workflow_id)` | Durable metadata-only handoff and claim events; same project only | Read-only |
| `decisionbound://project` | Project identity metadata | Read-only resource |

There is no `execute_shell`, `write_file`, `install_skill`, `approve_decision`, `change_user`, or `switch_project` tool.

## Workspace and authorization boundary

The server operator binds it to an existing repository at launch. Tools never accept a project-root argument from the model. Every invocation reads from the verified, registered root; hidden paths, excluded directories, secret-like file names (an exact set such as `credentials.json` and `id_rsa`, plus anchored patterns such as `*.pem`, `*.key`, `*.env`, `secrets.json` and `service-account*.json`, all matched without regard to case), names with a trailing dot or space or a colon, symlinked paths, binary files and oversized files are denied by the host. Ordinary names such as `environment.md`, `key_manager.py` and `secrets_policy.md` stay readable. The same rules apply to `search_project`, which skips such files.

**Local stdio is trusted as the host operating-system user.** Any MCP host connected to it can access the exposed read-only repository data. This is not equivalent to multi-tenant authorization, and a remote MCP endpoint should not be deployed without a separately reviewed identity/auth implementation.

## Tests and limitations

- Handler contract tests use an injected fake server to validate registration, root restrictions, and tool behavior without requiring network access.
- The CI protocol job installs `mcp>=2,<3` and performs real in-memory `Client(server)` discovery and tool invocation.
- If the SDK is unavailable locally, the real protocol test is skipped; it must pass in release CI before claiming validated MCP interoperability.
- No remote Streamable HTTP endpoint or third-party MCP **client** adapter is implemented in this sprint. Those remain product backlog items.

References: [official SDK](https://py.sdk.modelcontextprotocol.io/get-started/first-steps/) · [server transports](https://py.sdk.modelcontextprotocol.io/run/) · [client transports](https://py.sdk.modelcontextprotocol.io/client/transports/).

##  workflow permissions

Workflow inspection tools are exposed for the SQLite-backed local runtime only. They first verify that the workflow belongs to the server's registered project ID. MCP cannot submit, claim, cancel or execute workflow tasks. Contract tests explicitly reject cross-project status and event requests. These handler tests **are not** substitutes for the optional real-SDK wire test.
