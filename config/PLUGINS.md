# Plugin registration protocol — planned

Plugins expose one or more typed ports declared in `CONFIGURATION_AND_EXTENSIBILITY.md`. Supported initial ports: storage, retrieval, embeddings, model, harness, skill, tool, orchestration policy, verifier, artifact sink, telemetry, and sandbox. Discover via Python package entry points, or consume remote capabilities via MCP; do not execute arbitrary Python files discovered from untrusted repositories.

Each manifest declares: `id`, `version`, `api_version`, `capabilities`, `permissions`, `configuration_schema`, `data_handling`, `error_contract`, `healthcheck`, `feature_flags`, and `known_limitations`. Loading first validates signature/trust rules and the manifest; executable plugins run under scoped credentials/sandbox, and must pass the independent conformance suite for their port. Private data never crosses providers on fallback without explicit authorization. This directory is a specification, not a plugin registry implementation.
