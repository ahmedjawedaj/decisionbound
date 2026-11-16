# Versioned contracts (RFC stage)

JSON Schema draft 2020-12, version 1.0. These are **proposed** contracts for research comparators and later runtime, not an implemented SDK guarantee. The baseline checker validates schema syntax and tests a few negative examples; implementation must add rich Pydantic models, migration, privacy and conformance tests. Unknown properties disallowed; credentials are intentionally absent from contracts.

Core records: `DecisionRecord`, `TransferDecision`, `TaskCapsule`, `ToolManifest`, `ExecutionEvent`, `Handoff`, `EvalResult`, `ReproducibilityManifest`. Always include deterministic event IDs, source hashes, review state and explicit approval at execution boundaries.

v1.4 adds proposed `project-identity.v1.json` (portable public metadata only) and
`io-envelope.v1.json` (artifact references and security labels), not runtime contracts in use.
