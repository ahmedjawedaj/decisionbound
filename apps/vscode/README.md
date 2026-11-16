# DecisionBound Local — VS Code developer alpha

This directory contains a **functional, dependency-free JavaScript extension source** for a VS Code Extension Development Host. It is **read-only**: the extension shows project sessions, stage state, export-review metadata and event cursors using the exact local runtime used by `dbound client` and the browser. It cannot execute code, approve exports, merge, push, call a model, or modify project files.

## Setup

1. Install DecisionBound Python with its optional web API: `pip install -e '.[web]'`; run `dbound init` in your repository.
2. Start the daemon: `dbound serve --host 127.0.0.1 --port 8765`.
3. Reveal the pairing token intentionally: `dbound client pair --show-token`.
4. Open this `apps/vscode/` folder in VS Code, start **Run Extension** (Extension Development Host), then open one **trusted** initialized project folder in that host.
5. Run **DecisionBound: Pair Local Runtime**, paste your token (VS Code SecretStorage), then open the **DecisionBound Sessions** Explorer view.
6. Select a workflow. The output channel displays safe stage statuses, current review decision and the event timeline. Use **DecisionBound: Forget Local Pairing Token** to disconnect.

`decisionbound.localUrl` defaults to `http://127.0.0.1:8765` and rejects any non-loopback URL. `decisionbound.pollSeconds` controls bounded polling only while a session is selected.

## Developer checks

```bash
node --check src/client.js
node --check src/extension.js
npm test
```

No `npm install` is needed for offline Node tests. Unit and integration-style tests inject mock VS Code host objects and HTTP replies. **This does not replace a real Extension Development Host smoke test**; see `../../CONNECTED_INTEGRATION_TEST_PLAN.md` and [full local-client guide](https://github.com/ahmedjawedaj/decisionbound/blob/main/site_docs/local-clients.md).

## Limitations

One trusted local workspace at a time, one user's paired loopback service, no Marketplace publication or prebuilt VSIX, and no remote TLS/multi-tenant auth. `src/client.js` enforces strict HTTP loopback origins and refuses redirects; `src/extension.js` stores tokens only in VS Code SecretStorage and clears state on workspace change. Approval actions remain only in the CLI and web control room.


## Connected host and VSIX gates ()

Run `npm run test:host` **after** installing `@vscode/test-electron@2` in a connected development environment. This launches the actual VS Code Extension Development Host and asserts that the extension activates and registers commands. On Linux CI it runs under `xvfb-run`. It is not equivalent to `npm test` and has **not** been locally certified in this restricted sandbox.

Use `npm run package:vsix` with a connected npm resolver to produce a reviewable `dist/decisionbound-local.vsix` via `@vscode/vsce@3`. The GitHub Actions `vscode-package` job uploads that artifact. Nothing is automatically published or signed. Confirm version, publisher identity, Apache-2.0 license and release security policy before any Marketplace publication.

Once paired, use **DecisionBound: Check Local Setup** to read the same redacted prerequisite report as `dbound doctor` and the authenticated local browser. It does **not** validate provider authentication or authorize an agent.
