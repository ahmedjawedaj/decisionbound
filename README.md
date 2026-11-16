# DecisionBound

Experimental local-first Python SDK and CLI for evidence-backed engineering decisions and bounded agent workflows.

**Alpha 0.13.0a1.** This is not a production-certified autonomous service. The interactive concept demo is a simulation. Windows full-suite compatibility and independent security review remain incomplete; consult actual checks before relying on a platform.

## Capabilities

- Versioned decision records, source provenance, explicit approval and conservative transfer between repositories.
- Python SDK/CLI, optional read-only MCP server, authenticated loopback web API and read-only VS Code extension.
- Local SQLite workflows with leases, heartbeats, conservative recovery, backups and explicit schema migration.
- Consent-bound coding/review contracts, disposable Git worktrees and optional Docker test adapters.

Remote provider calls and source writes require explicit authorization. Static documentation does not run the agent backend.

## Install and verify

Use Python 3.11+ and Git; Node.js 22 is used for extension and demo checks.

```bash
python -m venv .venv
# Activate the environment for your operating system.
python -m pip install -e '.[test,web,docs,release,mcp]'
dbound --help
python -m pytest -q --cov=decisionbound --cov-fail-under=80
python scripts/check_publication.py
python scripts/generate_api_docs.py
python scripts/build_site.py
python scripts/verify_docs.py
```

See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), [Roadmap](ROADMAP.md) and [public content policy](PUBLICATION_POLICY.md). Read the user guides under `site_docs/`. Source, documentation and tests describe the implemented behavior; no comparative research result or production certification is claimed.

Original project material is licensed under [Apache-2.0](LICENSE). See [NOTICE](NOTICE), [CITATION.cff](CITATION.cff), [Governance](GOVERNANCE.md) and [Code of Conduct](CODE_OF_CONDUCT.md). Third-party dependencies and data retain their own terms.
