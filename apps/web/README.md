# DecisionBound local web control room

**Implemented inside `src/decisionbound/web.py`** as an optional FastAPI service. `dbound serve --host 127.0.0.1 --port 8765` serves one authenticated, loopback-only UI shared with the CLI and the editor. Features include workflow inspection, a project-scoped session list, metadata-only incremental event watching, profile inspection and explicit human patch-review approvals. The review panel never starts workers or merges/deploys patches.

The UI uses a local pairing token obtained by running `dbound client pair --show-token`. Browser localStorage is not used. `apps/web/` is reserved for a potential separately bundled UI, and does not itself contain a React application. GitHub Pages hosts **static documentation only**, not this live control interface.

See [local client synchronization](../../site_docs/local-clients.md) and [review console](../../site_docs/review-console.md).
