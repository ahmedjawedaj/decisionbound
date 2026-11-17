# Contributing to DecisionBound

DecisionBound is an experimental developer/research platform. Small, verifiable changes with clear boundaries are more useful than speculative rewrites.

## Contributions

Use issue templates for reproducible bugs, scoped features and falsifiable research proposals. Discuss public API or architecture changes first. Negative results and counterexamples are welcome. Report sensitive vulnerabilities privately through the Security tab.

## Setup and verification

Use Python 3.11+, Git and Node.js 22 for extension/demo checks.

```bash
git clone https://github.com/ahmedjawedaj/decisionbound.git
cd decisionbound
python -m venv .venv
# Activate .venv, then:
python -m pip install -e '.[test,web,docs,release]'
git switch -c your-scoped-change
python -m pytest -q --cov=decisionbound --cov-fail-under=80
python scripts/check_publication.py
python scripts/generate_api_docs.py
python scripts/build_site.py
python scripts/verify_docs.py
python scripts/check_docs.py
python scripts/release_roundtrip.py
node --test tests/site-demo.test.mjs
```

Run `npm run check` and `npm test` in `apps/vscode`. Real Docker, MCP, browser and editor-host gates require their actual prerequisites; report unsupported gates rather than replacing them with mocks.

## Pull requests

1. Work on a scoped branch. Describe the concrete problem, resulting behavior and verification.
2. Add meaningful regression/boundary tests for behavior changes; separate unrelated refactors.
3. Update public docstrings, generated documentation, affected release metadata and handoffs.
4. State failures, skips and environment limits. Never invent completed-task or benchmark claims.
5. Wait for required CI and resolve review findings. Never bypass protections or force-push shared history.

Public extension ports live in `src/decisionbound/ports.py`. Overrides must preserve external authorization, project scope and independent verification. Include expected and adversarial cases.

## Research and data

Research changes need explicit hypotheses, baselines, data provenance, held-out splits, costs and failure cases. Holdouts must stay hidden from evaluated agents. Never commit credentials, private code, local databases, event traces or proprietary data.

## Authorship

Every commit is authored by a person. The `quality-gate` job runs `scripts/check_attribution.py` on each pull request and fails when a commit has an assistant as author or committer, a `Co-Authored-By` or `Claude-Session` trailer naming an assistant, or when the title or body carries a generated-by footer. A squash merge builds its message from the title and body, so both are checked. Plain mentions of a model in prose are fine.

## License and governance

Contributions must be yours to submit and compatible with Apache-2.0. Third-party code/data requires a separate provenance/license review. Linking to a paper does not grant permission to copy its implementation.

Ahmed Jawed maintains the project. See GOVERNANCE.md, CODE_OF_CONDUCT.md and PUBLICATION_POLICY.md.
