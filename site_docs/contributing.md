# Contributing and code quality

## Working principles

**Assumptions → small patch → failing regression test → implementation → verification → review.** Keep changes surgical, avoid speculative abstraction and remove only unused code introduced by your patch. Follow CONTRIBUTING.md and PUBLICATION_POLICY.md.

## Development commands

```bash
python -m pip install -e '.[test,web,docs]'
pytest -q tests/unit tests/integration tests/security tests/contract
python scripts/generate_api_docs.py
python scripts/build_site.py
python scripts/verify_docs.py
python -m compileall -q src/decisionbound
```

## Override class expectations

- Implement `DecisionRepository`, `TransferPolicy`, `ContextCompiler`, `AgentWorker` or `TaskVerifier` explicitly.
- Give every public class and method a docstring describing parameters, side effects, authority rules and failure behavior.
- Don't override or bypass trust gates: source hash, reviewed status, project ownership, protected context, and tool allowlists.
- Add a contract test for each custom port and an integration test for each transport client.
- Make all provider calls opt-in and test with mocks; CI should not consume paid tokens.
- Keep benchmark held-out cases inaccessible to implementation agents.

## Review and release gates

Pull requests should include a reasoned design decision, test evidence, docs updates and security notes. CI must test on more than one Python version. Public release also requires an explicit license decision, package publishing approvals, a real VS Code/MCP compatibility test plan, and independent security review.

GitHub Pages hosts static documentation only; it must not execute agent code or act as an authenticated web control plane.


## Public repository and license

Original DecisionBound material is Apache-2.0. See the canonical [contribution guide](https://github.com/ahmedjawedaj/decisionbound/blob/main/CONTRIBUTING.md), [governance](https://github.com/ahmedjawedaj/decisionbound/blob/main/GOVERNANCE.md), [citation metadata](https://github.com/ahmedjawedaj/decisionbound/blob/main/CITATION.cff) and [security policy](https://github.com/ahmedjawedaj/decisionbound/security/policy). Third-party code/data terms remain separate.
