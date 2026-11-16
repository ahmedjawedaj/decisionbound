# DecisionBound

**Portable engineering judgment for AI agents.** A local-first experimental platform for evidence-backed engineering decisions, selective context reuse, and audited, budget-limited tool execution.

> **Status: alpha `0.13.0a1` (not production-ready).** Source includes an opt-in model-driven read-only agent, deterministic model-free demo, optional MCP integration, CLI/local API, conservative transfer, a fenced local background coordinator and state recovery/migration checks. Full worker-process containment, live-provider certification, distributed coordination, multi-day endurance and reproducible research comparisons remain open.

[Source and contribution guide](https://github.com/ahmedjawedaj/decisionbound) · [Public roadmap](https://github.com/ahmedjawedaj/decisionbound/blob/main/ROADMAP.md) · [Apache-2.0 license](https://github.com/ahmedjawedaj/decisionbound/blob/main/LICENSE) · [Citation](https://github.com/ahmedjawedaj/decisionbound/blob/main/CITATION.cff)

## Start without API keys

```bash
git clone https://github.com/ahmedjawedaj/decisionbound.git
cd decisionbound
python -m venv .venv && source .venv/bin/activate
python -m pip install .
dbound --help
```

Then follow the [quickstart](getting-started.md): a 60-second, offline demo of recording a decision, approving it and seeing how three repositories may reuse it. Its output is checked by a test on every change.

## New in durable local workflows

Build a bounded dependency graph, attach role-specific workers, and inspect verified task handoffs across CLI, local API and MCP without copying huge context windows. Atomic SQLite leases prevent duplicate local claims. Uncertain code writes are quarantined instead of silently retried. [Read the orchestration guide](orchestration.md).

## What works today

- **Review before learning:** import architecture decision candidates, compare source hashes and approve explicitly.
- **Selective transfer:** apply/translate/abstain/escalate with conservative project and owner boundaries.
- **Context economics:** compile bounded approved context without dropping protected constraints.
- **Real model planner:** opt-in, strict action envelope, configurable token/dollar/call limits, read-only tool allowlist.
- **Independent tool receipt checks:** host-observed SHA-256 receipts, no false `task_completed` assertion.
- **MCP v2 integration:** project-bound read-only server via official Python SDK (optional installation).
- **Safe extensibility:** abstract ports for storage, transfer, context, workers, verifiers and model transports.
- **Shared local state:** CLI and authenticated localhost API share profiles, projects and run traces.

## Documentation paths

[Quickstart](getting-started.md) · [Agent execution](agent-execution.md) · [MCP integration](mcp-integration.md) · [Verification](verification.md) · [Python SDK](sdk.md) · [API reference](api-reference.md) · [Security](security.md) · [Research](research.md).

## Evidence so far

On the real-repository benchmark, [CarryBench](https://github.com/ahmedjawedaj/decisionbound/blob/main/benchmarks/carrybench/README.md), the shipped transfer policy does not beat simple baselines, and its labels come from LLM annotators with no human labelling. Read the results before relying on any claim.

## Scientific contribution under test

The research question is not *can an agent remember a rule?* It is **when should a previous engineering decision transfer to another repository, and when should the agent abstain?** The present implementation remains a deterministic baseline. It does not claim improved coding accuracy or cost compared with native harnesses.

## reviewed code changes

The new [coding-agent pipeline](coding-agents.md) handles opt-in Git patch proposals, detached worktree isolation, test receipts, and human-controlled patch export. [Sandboxing and threat boundaries](sandboxing.md) explain what is independently tested and what still needs connected Docker validation.

## verifiable, bounded repair

[Repair loops](repair-loops.md) add operator-authored acceptance contracts, repeatable mock repair attempts, transactionally recorded checkpoints, conservatively reserved paid-model budgets, and failure reconciliation. [Native adapters](native-adapters.md) document optional *proposal-only* Claude/Codex CLI integrations; these are not full managed harness workers and have not been live-tested.

**The system does not claim semantic task completion or unattended production readiness.**

## shared local clients

The [Local clients & VS Code guide](local-clients.md) covers the implemented versioned loopback API, CLI, session browser, VS Code source extension, SecretStorage pairing, event cursors and connected-test limitations.


## first-run experience and connected integration gates

End users can now use `dbound setup` and `dbound doctor` to select safe default/provider profiles, explicitly initialize a repository, and inspect prerequisites without exposing API keys. The local web control room and VS Code use the same status. CI contains real-browser, VS Code host, VSIX packaging and earlier Docker/MCP gates; their results are not assumed passed. See [End-user setup and readiness](setup-diagnostics.md).


## installed-host certification

A [real installed-wheel certification](host-certification.md) checks Linux CLI setup, workflow persistence, state isolation, and the authenticated Node client against a real Uvicorn server. GitHub Actions is configured to execute the same script on Windows, Linux, and macOS, and to launch VS Code on hosted Windows/Linux runners. Read the [Actions results](https://github.com/ahmedjawedaj/decisionbound/actions) for the exact commit you use. A passing run is not assumed.
