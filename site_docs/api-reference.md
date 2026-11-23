# Generated Python API reference

> Generated from `src/decisionbound/*.py` docstrings by `scripts/generate_api_docs.py`.
> API is alpha; versioned compatibility guarantees are not yet published.

## `decisionbound.models`

Versioned, serialization-friendly domain objects used by DecisionBound.

Models are intentionally immutable to keep approvals, transfers and run inputs
reproducible. Changes should create a new value rather than silently altering an
approved decision while another client is reading it.

### `Evidence`

Hash-linked source evidence for an engineering decision.

Attributes:
    relative_path: Path relative to the originating repository. Absolute
        paths and paths traversing outside the project are not accepted.
    sha256: SHA-256 of the original source bytes at review time.
    kind: Evidence category, such as ``adr`` or ``human_review``.

### `Project`

A logical project with an explicit scope and set of technologies.

``project_id`` is an identifier, **not** an authorization token. A client
needs permission from the runtime even if it knows the ID.

### `Decision`

An approved or unapproved engineering preference with provenance.

``scope`` may be ``project`` or ``personal``. Project-scoped decisions are
not carried across repositories. Personal decisions may transfer only when
their explicit applicability and the target's policies permit it.

``required_tags`` denotes all required target technologies. Empty means
technology-agnostic. ``blocked_tags`` denotes explicit exclusions.

#### `Decision.to_dict`

```python
to_dict() -> dict
```

Return a JSON-safe representation without mutating the decision.

#### `Decision.from_dict`

```python
from_dict(cls, record: dict) -> Decision
```

Restore a decision from a store record or a reviewed export.

### `TransferResult`

Explainable decision about reuse in a target project.

A transfer result is advisory; it is not permission to perform a write.

### `ContextItem`

Candidate context material with a verifiable source pointer.

``protected`` items cannot be dropped to satisfy a token budget. If they
do not fit the compiler raises :class:`ContextBudgetExceeded`.

### `ContextCapsule`

Immutable output of context selection for one task.

#### `ContextCapsule.to_dict`

```python
to_dict() -> dict
```

Serialize a capsule for CLI, API, or benchmark output.

### `WorkerResult`

A bounded worker result, separate from verified task completion.

## `decisionbound.ports`

Stable extension contracts for alternate storage, policies and workers.

Prefer implementing these ports instead of subclassing private concrete
classes. Every method documents expected behavior; contract tests supplied
under ``tests/contract`` can be reused by third-party adapters.

### `DecisionRepository`

Persistence boundary for versioned projects, decisions and profiles.

Implementations **must** reject stale revision updates and must never
silently promote a candidate decision to approved status.

#### `DecisionRepository.add_project`

```python
add_project(project: Project) -> None
```

Persist a project or reject a conflicting project identifier.

#### `DecisionRepository.get_project`

```python
get_project(project_id: str) -> Project | None
```

Return a project by identifier, or ``None`` if absent.

#### `DecisionRepository.add_decision`

```python
add_decision(decision: Decision) -> None
```

Persist a new candidate decision with its immutable evidence.

#### `DecisionRepository.get_decision`

```python
get_decision(decision_id: str) -> Decision | None
```

Look up a decision without changing its approval status.

#### `DecisionRepository.list_decisions`

```python
list_decisions(project_id: str | None = None) -> list[Decision]
```

Return decisions in a deterministic order.

#### `DecisionRepository.set_decision_status`

```python
set_decision_status(decision_id: str, status: str, expected_revision: int) -> Decision
```

Atomically update status or reject a stale revision.

### `TransferPolicy`

Overrideable compatibility and authority checks for project transfer.

#### `TransferPolicy.evaluate`

```python
evaluate(decision: Decision, target: Project, forbidden: Iterable[str] = ()) -> TransferResult
```

Choose apply/translate/abstain/escalate with a human-readable reason.

### `ContextCompiler`

A swappable strategy that selects task-relevant information.

#### `ContextCompiler.compile`

```python
compile(task: str, items: Iterable[ContextItem], max_tokens: int) -> ContextCapsule
```

Produce a bounded context capsule; never silently omit protected inputs.

### `AgentWorker`

Limited execution adapter; dispatchers must not assume completion is verified.

#### `AgentWorker.run`

```python
run(task: str, context: ContextCapsule) -> WorkerResult
```

Execute one bounded step or task without exceeding assigned permissions.

### `TaskVerifier`

Independent outcome checker for a worker-produced artifact.

#### `TaskVerifier.verify`

```python
verify(task: str, result: WorkerResult) -> bool
```

Return objective success based on external evidence, not self-report.

## `decisionbound.storage`

Local transactional SQLite implementation of the repository port.

This adapter is intended for a single-user runtime. It never attempts to
implement distributed leases or multi-tenant authorization. All updates are
parameterized and optimistic concurrency is enforced for decisions/profiles.

### `RevisionConflict`

Raised when an update is based on an obsolete entity revision.

### `SQLiteRepository`

Persist DecisionBound state in a user-owned SQLite database.

Args:
    path: Database path. Parent directories are created with restrictive
        permissions when possible. Symlinked database targets are rejected.

Override guidance:
    Use :class:`DecisionRepository` for a PostgreSQL adapter. Extending
    this class directly is supported for local experimentation, but schema
    migrations and isolation guarantees are private implementation details.

#### `SQLiteRepository.add_project`

```python
add_project(project: Project) -> None
```

Register a logical project and reject conflicting duplicate IDs.

#### `SQLiteRepository.get_project`

```python
get_project(project_id: str) -> Project | None
```

Retrieve a project without trusting a local manifest as authorization.

#### `SQLiteRepository.add_decision`

```python
add_decision(decision: Decision) -> None
```

Save a new decision; existing decision IDs are never overwritten.

#### `SQLiteRepository.get_decision`

```python
get_decision(decision_id: str) -> Decision | None
```

Load a decision from its canonical JSON representation.

#### `SQLiteRepository.list_decisions`

```python
list_decisions(project_id: str | None = None) -> list[Decision]
```

List decisions deterministically; optional source-project filter.

#### `SQLiteRepository.set_decision_status`

```python
set_decision_status(decision_id: str, status: str, expected_revision: int) -> Decision
```

Perform compare-and-swap approval/revocation in one transaction.

#### `SQLiteRepository.bind_project_root`

```python
bind_project_root(project_id: str, canonical_root: Path) -> None
```

Bind a registered project ID to exactly one verified local checkout path.

A copied `.decisionbound/project.yaml` is not sufficient to access an
existing project's state. Registration is first-writer-wins under the
SQLite writer transaction; relocating a project requires an explicit
future migration and cannot be inferred from an identical manifest.

#### `SQLiteRepository.require_project_root`

```python
require_project_root(project_id: str, canonical_root: Path) -> None
```

Require current checkout binding; never authorize an unregistered clone.

Old local databases with no binding need an explicit `dbound init`
invocation to enroll their existing manifest before routine use.

#### `SQLiteRepository.get_setting`

```python
get_setting(key: str, default: str | None = None) -> str | None
```

Get a user-scoped setting, e.g. the active profile name.

#### `SQLiteRepository.set_setting`

```python
set_setting(key: str, value: str) -> None
```

Atomically update a user-scoped setting.

#### `SQLiteRepository.list_profiles`

```python
list_profiles() -> list[dict]
```

Return custom profiles, excluding secret values (never persisted).

#### `SQLiteRepository.get_profile`

```python
get_profile(name: str) -> dict | None
```

Retrieve a custom profile's public configuration.

#### `SQLiteRepository.save_profile`

```python
save_profile(profile: dict, expected_revision: int | None = None) -> dict
```

Create or revision-check a profile without storing credentials.

#### `SQLiteRepository.record_event`

```python
record_event(run_id: str, event: str, payload: dict) -> int
```

Append a durable, timestamped run event.

#### `SQLiteRepository.events`

```python
events(run_id: str) -> list[dict]
```

Read events in insertion order for deterministic replay.

## `decisionbound.identity`

Repository identity and provenance utilities.

Project manifests are *discovery hints*, not proofs of authorization. The local
runtime maintains authoritative project records and rejects ID collisions.

### `safe_relative_path`

```python
safe_relative_path(root: Path, relative: str) -> Path
```

Resolve a user-specified repository file without escaping the root.

Raises:
    ValueError: An absolute, parent-traversal or symlink escape is present.

### `hash_evidence`

```python
hash_evidence(root: Path, relative: str, kind: str = 'adr') -> Evidence
```

Create an immutable content fingerprint without uploading file contents.

### `init_project`

```python
init_project(root: Path, repository: SQLiteRepository, name: str | None = None, technologies: tuple[str, ...] = (), owner: str = 'local') -> Project
```

Initialize a project manifest once and register it with the local store.

Existing manifests are read and checked rather than rewritten. A copied
manifest with a conflicting name/owner cannot silently hijack an ID.

### `load_project`

```python
load_project(root: Path, repository: SQLiteRepository) -> Project
```

Load and verify an initialized project against the local registry.

## `decisionbound.profiles`

User-facing provider/harness presets and safe, shareable custom profiles.

A profile is public configuration. Credentials are resolved separately from
environment variables or a secret manager; they are never embedded in exports.

### `InvalidProfile`

Invalid, unsafe or unsupported user profile definition.

### `validate_profile`

```python
validate_profile(data: dict) -> dict
```

Validate a shareable profile and reject embedded secrets.

Unknown properties fail closed. This avoids future versions silently
interpreting a formerly ignored field as an enabled capability.

### `ProfileManager`

Resolve defaults and custom overlays through one durable registry.

Default precedence: safe built-in -> custom overlay -> explicit invocation
flags. Organization policy is a separate runtime boundary and cannot be
weakened via profile import, even if a YAML file requests it.

#### `ProfileManager.names`

```python
names() -> list[str]
```

List built-in and custom selectable names.

#### `ProfileManager.resolve`

```python
resolve(name: str) -> dict
```

Return a validated, merged profile without secrets or side effects.

#### `ProfileManager.active`

```python
active() -> str
```

Get the shared active profile or the safe offline default.

#### `ProfileManager.use`

```python
use(name: str) -> dict
```

Switch the profile for subsequent tasks; existing runs stay pinned.

#### `ProfileManager.create`

```python
create(name: str, extends: str = 'local-safe', **overrides) -> dict
```

Create a validated custom profile from a built-in template.

#### `ProfileManager.update`

```python
update(data: dict, expected_revision: int) -> dict
```

Save a reviewed edit with optimistic concurrency control.

#### `ProfileManager.export`

```python
export(name: str, path: Path) -> None
```

Write a credential-free YAML profile; reject symlink destinations.

#### `ProfileManager.import_file`

```python
import_file(path: Path) -> dict
```

Import YAML data only after schema and secret validation.

## `decisionbound.transfer`

Safe-by-default transfer decisions with explicit project authority.

### `ConservativeTransferPolicy`

Deterministic transfer policy suitable for auditable baseline studies.

It does **not** infer that a shared Git repository means shared authority.
Cross-project personal transfer requires the same explicitly named owner;
project-scoped decisions never transfer. Higher-priority target policy
conflicts are treated as escalation rather than opportunities to guess.

Subclass and override :meth:`evaluate` to implement an experimental model
or a domain-specific organization policy. Test subclasses against the
authority and negative-transfer contract suite before deployment.

#### `ConservativeTransferPolicy.evaluate`

```python
evaluate(decision: Decision, target: Project, forbidden: Iterable[str] = ()) -> TransferResult
```

Return a reasoned action without changing any project files.

Args:
    decision: Approved, evidence-linked candidate for reuse.
    target: Target project and its declared technology tags.
    forbidden: Authoritative blocked decision IDs set by the target
        owner; never populated from an untrusted retrieved document.

Returns:
    One of ``apply``, ``translate``, ``abstain`` or ``escalate``.

## `decisionbound.context`

Deterministic task-context selection with non-negotiable protected slots.

### `ContextBudgetExceeded`

Protected requirements exceed a token budget and cannot be omitted.

### `estimated_tokens`

```python
estimated_tokens(text: str) -> int
```

Heuristic token estimate (ceil(chars/4)); *not* provider tokenization.

### `SelectiveContextCompiler`

Small, explainable compiler that never truncates protected evidence.

The base compiler is intentionally lexical and deterministic. To add
semantic RAG, subclass :class:`ContextCompiler` and implement ``compile``
while retaining the protected-input and provenance contract.

#### `SelectiveContextCompiler.compile`

```python
compile(task: str, items: Iterable[ContextItem], max_tokens: int) -> ContextCapsule
```

Select whole context items by priority then lexical task relevance.

Raises:
    ValueError: Budget is non-positive or inputs have duplicate keys.
    ContextBudgetExceeded: Task and protected constraints do not fit.

## `decisionbound.skills`

Low-cost, trust-aware discovery of project and personal skill manifests.

### `Skill`

A discoverable skill descriptor; content is loaded only on selection.

### `SkillRegistry`

Discover ``SKILL.md`` files without loading full text into context.

Skill files are *untrusted content*, not executable or policy-authoritative
instructions. Selection does not grant shell/network/tool permissions.

#### `SkillRegistry.discover`

```python
discover() -> list[Skill]
```

Return metadata only, sorted by name and with duplicate names removed.

#### `SkillRegistry.load`

```python
load(skill: Skill, max_chars: int = 12000) -> str
```

Read a selected skill with a size cap; no script execution.

## `decisionbound.tools`

Strict-schema, least-privilege registry for tools used by read-only agents.

### `ToolPermissionDenied`

An agent attempted to call a tool without explicit authorization.

### `ToolSchemaError`

A tool's argument shape or value type failed strict validation.

### `RegisteredTool`

A tool implementation and its strict, inspectable argument contract.

Args:
    name: Unique tool identifier.
    description: Human-readable purpose, not authorization policy.
    handler: Host-side function that receives already validated arguments.
    side_effects: True if the tool can change state outside the agent.
    arg_types: Exact required field names and Python scalar types. Extra
        fields, missing values, and incorrect types always fail closed.

### `ToolRegistry`

Allowlisted strict-schema tool broker, independent of prompt content.

Unknown tools and write-capable tools fail closed by default. Tool
descriptions are filtered before disclosure to keep context smaller.

#### `ToolRegistry.register`

```python
register(tool: RegisteredTool) -> None
```

Register a unique, schema-checked tool without granting permission.

#### `ToolRegistry.describe`

```python
describe(allowed: set[str]) -> list[dict[str, Any]]
```

Return only allowlisted schemas and metadata, not every registry tool.

#### `ToolRegistry.call`

```python
call(name: str, args: dict[str, Any], allowed: set[str], allow_writes: bool = False) -> dict[str, Any]
```

Validate exact argument schema and host permissions before invoking.

Raises:
    ToolPermissionDenied: Tool name or effect is not authorized.
    ToolSchemaError: Arguments are missing, extra or incorrectly typed.

## `decisionbound.agent_loop`

Bounded read-only tool loop with pluggable planning, suitable for dry runs.

This is an *execution kernel*, not a generative coding agent. Providers can
implement Planner.next_action, but the host always enforces allowlists, effects,
step limits and observation caps independent of any model response.

### `RepeatedCallError`

The planner proposed the same tool call too many times in a row.

### `AgentDeadlineExceeded`

The loop's own wall-clock deadline passed before the next planner or tool call.

### `neutralise_markers`

```python
neutralise_markers(text: str) -> str
```

Break up any run of three or more ``<`` or ``>`` so content cannot forge a marker.

### `render_untrusted_observation`

```python
render_untrusted_observation(observation: str, allowed_tools: frozenset[str] | set[str], limit: int = 3000) -> str
```

Present one stored observation to a model as marked, escaped, visibly cut data.

The tool name sits outside the markers and is shown only when it is one of
``allowed_tools``. The content between the markers is the stored observation
with marker-like runs neutralised and cut to ``limit`` characters. A note
after the closing marker says when the content was cut, either here or by
the loop's own cap (the loop stores JSON objects, so text that starts with ``{`` but is not valid JSON was cut). The stored
observation, its hash and the event ledger are not changed by this.

### `ToolAction`

Strict action proposed by a planner and revalidated by the runtime.

### `Finish`

Planner's terminal message; not independently verified task completion.

### `Planner`

Overrideable decision interface for one action at a time.

A model planner should validate provider output against a strict schema
before returning a ToolAction or Finish. Never execute raw model text as
shell commands or Python source.

#### `Planner.next_action`

```python
next_action(task: str, context: ContextCapsule, observations: tuple[str, ...]) -> ToolAction | Finish
```

Propose one allowlisted tool invocation or a terminal response.

### `BoundedReadOnlyAgent`

Run a planner under host-controlled tool, step and observation budgets.

Args:
    planner: Provider-independent planning implementation.
    tools: Registry of strictly typed host tool handlers.
    allowed_tools: Explicit capability names allowed for this run.
    max_steps: Hard upper bound on tool calls, from 1 to 25.
    max_observation_chars: Per-result cap before returning to planner.
    max_identical_calls: How many times in a row the same tool and
        arguments may run, from 1 to 25. The next identical proposal
        raises ``RepeatedCallError`` before the tool runs. Default 3.
    deadline_seconds: Optional wall-clock limit for the whole loop,
        checked before each planner call and before each tool call. It
        cannot interrupt a planner or tool call that is already running.
        Off by default. Raises ``AgentDeadlineExceeded``.
    clock: Monotonic clock, replaceable in tests.

Side effects:
    None by default. Every call uses ``allow_writes=False`` regardless
    of whether the planner asks for a side-effecting tool.

#### `BoundedReadOnlyAgent.run`

```python
run(task: str, context: ContextCapsule) -> WorkerResult
```

Execute a bounded loop and return observations without claiming verification.

Raises:
    ToolPermissionDenied: The planner selected a disallowed tool.
    RepeatedCallError: The same call was proposed more than
        ``max_identical_calls`` times in a row. Subclass of RuntimeError.
    AgentDeadlineExceeded: ``deadline_seconds`` passed. Subclass of RuntimeError.
    RuntimeError: Maximum steps were exhausted without a Finish.

### `repository_readonly_tools`

```python
repository_readonly_tools(root: Path, max_file_bytes: int = 512000) -> ToolRegistry
```

Create safe local file inspection/search tools for a single project root.

This is a bounded inspection utility, not a secure sandbox against code
executing with the same operating-system permissions as the process.

### `SearchThenFinishPlanner`

Deterministic demo planner used to exercise a tool loop offline.

It is not a language model or a substitute for a trained agent planner.

#### `SearchThenFinishPlanner.next_action`

```python
next_action(task: str, context: ContextCapsule, observations: tuple[str, ...]) -> ToolAction | Finish
```

Search once, then return a user-readable observation summary.

## `decisionbound.model_planner`

Strict, budgeted model planning with transport inversion and no implicit network.

The LLM proposes one JSON action; the host enforces authorization and never
executes raw text. Plug in a fake transport for deterministic local tests or
an explicitly configured remote transport after the user authorizes spending.

### `ModelOutputError`

A model response is not a valid and permitted action envelope.

### `ModelBudgetExceeded`

A request would exceed a strict configured token, dollar or time bound.

### `ProviderResponseError`

An authorized provider request failed without exposing credentials.

### `parse_action`

```python
parse_action(raw: str, available: set[str]) -> ToolAction | Finish
```

Validate a complete JSON envelope and its tool name, never partial JSON.

This host-side validation is required even when the model provider advertises
JSON-schema-constrained generation. Model output is always untrusted.

### `ModelCompletion`

Model text and optional provider-reported token metering.

A missing token count is never interpreted as free usage.

### `JSONModelTransport`

Override to connect an LLM that returns one JSON action per request.

Implementations must not retry paid requests automatically after ambiguous
timeouts. Return the provider's actual token usage whenever available.

#### `JSONModelTransport.complete`

```python
complete(*, messages: list[dict[str, str]], schema: dict[str, Any], max_output_tokens: int) -> ModelCompletion
```

Request a JSON action from a configured, explicitly enabled model.

### `Pricing`

Explicit USD rates per million tokens; prevents invisible free assumptions.

#### `Pricing.cost`

```python
cost(input_tokens: int, output_tokens: int) -> float
```

Compute an upper bound or measured USD charge from token counts.

### `ModelLimits`

Host-side model planning limits, separate from the tool loop's limits.

``max_input_tokens`` is enforced with a deliberately conservative character
estimate, not a tokenizer. Usage overruns from a dishonest provider are
detected only after its response, so production rate enforcement also needs
gateway-side limits.

### `ModelAccounting`

Mutable, per-run counters without secrets or raw prompt contents.

### `BudgetedModelPlanner`

Selectively disclose tools and compile a bounded, strictly parsed action.

Only tools in ``allowed_tools`` are advertised. Host tool execution still
checks that allowlist independently. The planner is **single-run stateful**:
create a new instance for each run rather than reusing accounting between
unrelated users or tasks.

#### `BudgetedModelPlanner.next_action`

```python
next_action(task: str, context: ContextCapsule, observations: tuple[str, ...]) -> ToolAction | Finish
```

Generate the next action while enforcing preflight worst-case spend.

### `ScriptedModelTransport`

Deterministic fake for tests and documentation; never calls a model.

#### `ScriptedModelTransport.complete`

```python
complete(*, messages: list[dict[str, str]], schema: dict[str, Any], max_output_tokens: int) -> ModelCompletion
```

Return the next queued response, rejecting unexpected extra calls.

## `decisionbound.providers`

Opt-in provider adapters; network and credentials are disabled by default.

The same transport contract accepts fake HTTP exchanges for deterministic
integration tests. These are *JSON action* adapters, not Claude/Codex SDK agent
adapters. Provider support is only claimed after live conformance testing.

### `OpenAICompatibleJSONTransport`

OpenAI Chat Completions style JSON-schema actions (incl. compatible gateways).

Requires explicit ``enabled=True``, environment-sourced credentials and
an approved HTTPS host. Usage and refusal handling are interpreted at the
transport boundary, but the host must still validate action arguments.

#### `OpenAICompatibleJSONTransport.complete`

```python
complete(*, messages: list[dict[str, str]], schema: dict[str, Any], max_output_tokens: int) -> ModelCompletion
```

Request one schema-constrained action; paid requests are never retried.

### `AnthropicJSONTransport`

Anthropic Messages adapter for JSON action generation.

The model instruction asks for JSON; this adapter does not claim native
strict JSON-schema enforcement. Host validation remains mandatory.

#### `AnthropicJSONTransport.complete`

```python
complete(*, messages: list[dict[str, str]], schema: dict[str, Any], max_output_tokens: int) -> ModelCompletion
```

Request one read-only planning action from Anthropic Messages.

## `decisionbound.execution`

Audited, bounded model-to-tool execution that never claims coding completion.

This module connects model planning, tool permissions, immutable task context,
SQLite traces and an independent read-only evidence verifier. Every request is
explicitly scoped to one initialized project and a per-run budget.

### `ReadOnlyEvidenceVerifier`

Check tool-observation provenance, not semantic task correctness.

This verifier cannot certify that an arbitrary model explanation is true,
and must never report code-change or user-task completion.

#### `ReadOnlyEvidenceVerifier.verify`

```python
verify(task: str, result: WorkerResult) -> bool
```

Require a prepared result and at least one structured tool observation.

### `ReadOnlyLedgerVerifier`

Cross-check worker evidence against independent host-side tool receipts.

This verifies that returned observations match tool executions recorded by
the runtime, preventing a worker from fabricating tool evidence in its
final answer. It does not verify semantic task correctness.

#### `ReadOnlyLedgerVerifier.verify`

```python
verify(task: str, result: WorkerResult) -> bool
```

Require matching SHA-256 hashes and counts in the durable ledger.

### `run_readonly_agent`

```python
run_readonly_agent(*, project_path: Path, store: SQLiteRepository, task: str, transport: JSONModelTransport, pricing: Pricing, limits: ModelLimits | None = None, max_context_tokens: int = 1200, allowed_tools: set[str] | None = None, workflow_task_ref: str | None = None) -> dict
```

Run a budgeted, model-driven read-only inspection with durable receipts.

Args:
    project_path: Verified local project; no cross-project reads.
    store: Durable event and project registry.
    task: A user request for repository inspection, not code modification.
    transport: Explicit provider adapter or scripted deterministic fake.
    pricing: Explicit input/output prices. Never infer a remote model is free.
    limits: Per-run preflight limits; calls are accounted conservatively.
    max_context_tokens: Budget for selected, approved repository context.
    allowed_tools: Subset of local read-only tools permitted for this run.

Returns:
    A run receipt, usage estimate and trace reference. ``task_completed``
    remains false pending real objective-specific verification.

## `decisionbound.mcp_server`

Read-only, project-bound MCP v2 server (optional official Python SDK).

This module imports without MCP installed; call ``create_mcp_server`` after
``pip install 'decisionbound[mcp]'``. A server instance is permanently scoped
to the initialized workspace supplied by its operator. The LLM does not get a
free-form ``project_path`` tool parameter and cannot change workspace roots.

### `create_mcp_server`

```python
create_mcp_server(project_path: Path, store: DecisionRepository | None = None, server_class: type | None = None) -> Any
```

Create an official-SDK-compatible MCP server for one trusted workspace.

Args:
    project_path: Trusted, previously initialized repository directory.
    store: Explicit state backend; default is the user's local registry.
    server_class: Test seam for validating tool and resource registration.

Security:
    The operator fixes a project root at server launch. Every tool uses
    that root; no arbitrary workspace selection, source writes, shell
    execution, remote fetches or credential disclosure are exposed.
    Local stdio inherits OS-user access; it is not a tenant authorization
    mechanism. Remote MCP transport requires separately implemented auth.

Raises:
    RuntimeError: The optional MCP v2 package is missing.
    PermissionError: The workspace identity does not match stored state.

### `serve_stdio`

```python
serve_stdio(project_path: Path) -> None
```

Serve the bound workspace on stdio; stdout is reserved for MCP frames.

## `decisionbound.services`

Application services connecting provenance, transfer and context ports.

### `EvidenceChanged`

Source document no longer matches its reviewed SHA-256 digest.

### `DecisionService`

Reusable coordination service with dependency-injected policies.

Args:
    repository: Persistent storage implementation.
    transfer_policy: Authority evaluator; defaults to conservative rules.
    compiler: Context selection policy; defaults to lexical budgeting.

Custom applications can override either policy without modifying this
service. Approval always checks the source evidence hash regardless of the
chosen transfer policy.

#### `DecisionService.discover_adr`

```python
discover_adr(project: Project, root: Path, relative: str, scope: str = 'project', owner: str | None = None) -> Decision
```

Create an unapproved candidate from an ADR; never auto-approve it.

An ADR must include sections titled ``Context`` and ``Decision``.
External source text is treated as evidence, not a trusted instruction.

#### `DecisionService.approve`

```python
approve(root: Path, decision_id: str, revision: int) -> Decision
```

Approve only when the original file content is still identical.

Raises:
    EvidenceChanged: Source was edited, replaced or removed.

#### `DecisionService.transfer`

```python
transfer(target: Project, forbidden: set[str] | None = None) -> list[TransferResult]
```

Evaluate all known decisions; never mutate target repository.

#### `DecisionService.prepare`

```python
prepare(task: str, target: Project, max_tokens: int = 1200, forbidden: set[str] | None = None) -> ContextCapsule
```

Compile the task and compatible approved principles into a capsule.

Only ``apply``/``translate`` results contribute statements. An external
authoritative policy can be included as a protected context item using
the injectable ``compiler`` for task-specific contexts.

## `decisionbound.runtime`

Bounded local execution with durable audit events and verification hooks.

This alpha implements an offline preparation worker, not a full coding agent.
It's deliberately designed so provider-specific workers can be swapped in
later without changing transport clients or weakening the verifier boundary.

### `OfflinePreparationWorker`

Useful, deterministic baseline for testing orchestration without keys.

#### `OfflinePreparationWorker.run`

```python
run(task: str, context: ContextCapsule) -> WorkerResult
```

Prepare an evidence summary; does not perform code modifications.

### `PreparationVerifier`

Verify that an offline worker prepared a budget-compliant capsule.

#### `PreparationVerifier.verify`

```python
verify(task: str, result: WorkerResult) -> bool
```

Validate the result state without labeling it task completion.

### `Supervisor`

Run one bounded worker, audit it, and separate preparation from success.

Args:
    repository: Shared event store.
    worker: Replaceable worker implementing :class:`AgentWorker`.
    verifier: Independent predicate, not the worker's self-report.

Invariant:
    ``verified_preparation`` does not mean the user's task was completed.

#### `Supervisor.run`

```python
run(task: str, context: ContextCapsule, run_id: str | None = None, max_steps: int = 1) -> dict
```

Execute bounded worker once, preserving error and verification traces.

## `decisionbound.settings`

Local runtime paths; overrides are explicit and scoped to this process.

### `state_dir`

```python
state_dir() -> Path
```

Return the user's private state directory or an explicit test override.

### `repository`

```python
repository() -> SQLiteRepository
```

Open the local state store without invoking remote services.

## `decisionbound.cli`

Human-oriented CLI for the safe local DecisionBound alpha.

The CLI uses the same services and SQLite registry as the optional web API.
Every command can operate without an LLM account or third-party credentials.

### `setup_user`

```python
setup_user(profile: str | None = typer.Option(None, '--profile', help='Built-in or custom end-user profile'), path: Path | None = typer.Option(None, '--path', help='Existing repository to inspect or initialize'), initialize: bool = typer.Option(False, '--initialize', help='Explicitly register this project'), interactive: bool = typer.Option(False, '--interactive', help='Prompt for a profile')) -> None
```

Choose an end-user profile; never save secrets or enable model calls.

Example: ``dbound setup --profile local-safe --path . --initialize``.
Native CLI and provider availability are reported, not assumed tested.

### `doctor`

```python
doctor(profile: str | None = typer.Option(None, '--profile'), path: Path | None = typer.Option(None, '--path'), strict: bool = typer.Option(False, '--strict', help='Exit nonzero if required prerequisites are missing')) -> None
```

Read-only prerequisite diagnostics with no credential disclosure.

This does not call provider endpoints or certify Docker/MCP integrations.
``--strict`` is suitable for local setup checks and smoke-test jobs.

### `init`

```python
init(path: Path = typer.Option(Path('.'), help='Existing project directory'), name: str | None = typer.Option(None), owner: str = typer.Option('local'), technologies: str = typer.Option('', help='Comma-separated technology tags')) -> None
```

Initialize a local project identity; never enable cloud services.

### `status`

```python
status(path: Path = typer.Option(Path('.'))) -> None
```

Inspect trusted project identity and active provider profile.

### `profiles_list`

```python
profiles_list() -> None
```

Show built-in and saved profiles without revealing credentials.

### `profiles_use`

```python
profiles_use(name: str) -> None
```

Select the active profile for future tasks in this user's runtime.

### `profiles_create`

```python
profiles_create(name: str, extends: str = typer.Option('local-safe', '--from')) -> None
```

Create a custom profile inheriting a reviewed built-in preset.

### `profiles_export`

```python
profiles_export(name: str, output: Path = typer.Option(..., '--output')) -> None
```

Export a portable, credential-free YAML profile.

### `profiles_import`

```python
profiles_import(path: Path) -> None
```

Validate and import a shared YAML profile; no permission escalation.

### `decision_discover`

```python
decision_discover(relative_path: str, project_path: Path = typer.Option(Path('.'), '--path'), scope: str = typer.Option('project')) -> None
```

Create an unapproved candidate by reading a local architecture record.

### `decision_approve`

```python
decision_approve(decision_id: str, revision: int = typer.Option(1), project_path: Path = typer.Option(Path('.'), '--path')) -> None
```

Approve reviewed evidence only if the source hash is still current.

### `decisions_list`

```python
decisions_list(project_path: Path = typer.Option(Path('.'), '--path')) -> None
```

List reviewed and unreviewed decisions owned by the current project.

### `transfer_preview`

```python
transfer_preview(path: Path = typer.Option(Path('.'), '--path')) -> None
```

Preview selective transfer; never write into target repository.

### `prepare`

```python
prepare(task: str, path: Path = typer.Option(Path('.'), '--path'), tokens: int = typer.Option(1200, '--tokens')) -> None
```

Create an explainable task capsule under a context-token budget.

### `run`

```python
run(task: str, path: Path = typer.Option(Path('.'), '--path'), tokens: int = typer.Option(1200, '--tokens')) -> None
```

Execute a bounded offline preparation worker and persist its trace.

### `trace`

```python
trace(run_id: str) -> None
```

Inspect the durable event log for a previous run.

### `skills`

```python
skills(path: Path = typer.Option(Path('.'), '--path')) -> None
```

Discover skill metadata without injecting their full text into context.

### `serve`

```python
serve(host: str = typer.Option('127.0.0.1'), port: int = typer.Option(8765)) -> None
```

Start a local control API; remote binding is intentionally prohibited.

### `agent_demo`

```python
agent_demo(task: str, path: Path = typer.Option(Path('.'), '--path')) -> None
```

Run a model-shaped, deterministic tool inspection without keys or network.

This demonstrates a complete planner → tool → verifier → trace execution,
but does not constitute a live language model or coding-task completion.

### `agent_run`

```python
agent_run(task: str, model: str = typer.Option(..., '--model'), provider: str = typer.Option('openai', '--provider'), path: Path = typer.Option(Path('.'), '--path'), allow_remote: bool = typer.Option(False, '--allow-remote'), input_price: float = typer.Option(..., '--input-price', help='USD per 1M input tokens'), output_price: float = typer.Option(..., '--output-price', help='USD per 1M output tokens'), max_cost: float = typer.Option(0.1, '--max-cost', help='Hard preflight USD cap'), max_steps: int = typer.Option(5, '--max-steps'), api_key_env: str = typer.Option('', '--api-key-env')) -> None
```

Opt in to a real provider's structured, read-only tool agent.

Keys are read only from the specified environment variable. No code edits,
shell commands, remote tools, retries or multi-agent delegation occur.
Provider prices must be supplied explicitly, and the run is not marked as
task-complete merely because the model returns a final answer.

### `mcp_serve`

```python
mcp_serve(path: Path = typer.Option(Path('.'), '--path')) -> None
```

Run an official-MCP-SDK-backed stdio server, bound to one project.

Never print CLI output to stdout before starting the MCP server. All
project data returned to clients is scoped to the verified workspace.

### `code_inspect`

```python
code_inspect(patch_file: Path = typer.Option(..., '--patch-file'), path: Path = typer.Option(Path('.'), '--path')) -> None
```

Read-only patch inspection; report changed paths without modifying Git.

### `code_mock_run`

```python
code_mock_run(task: str, patch_file: Path = typer.Option(..., '--patch-file'), path: Path = typer.Option(Path('.'), '--path'), approve_writes: bool = typer.Option(False, '--approve-writes')) -> None
```

Demonstrate isolated patch application with a fake test runner.

This deliberately cannot certify code correctness or task completion.
No Docker daemon, LLM credentials or network access are involved.

### `code_apply`

```python
code_apply(task: str, patch_file: Path = typer.Option(..., '--patch-file'), path: Path = typer.Option(Path('.'), '--path'), test_cmd: list[str] = typer.Option([], '--test-cmd', help='Repeatable command; parsed into argv, not a host shell'), approve_writes: bool = typer.Option(False, '--approve-writes'), allow_docker: bool = typer.Option(False, '--allow-docker'), image: str = typer.Option('', '--image'), timeout: int = typer.Option(120, '--timeout')) -> None
```

Apply a reviewed text patch in an isolated worktree, then Docker-test it.

Requires both explicit write consent and local sandbox consent. The host
never executes the test command and never changes the source checkout.

### `code_propose`

```python
code_propose(task: str, target: list[str] = typer.Option([], '--target'), path: Path = typer.Option(Path('.'), '--path'), provider: str = typer.Option('openai', '--provider'), model: str = typer.Option(..., '--model'), api_key_env: str = typer.Option('', '--api-key-env'), input_price: float = typer.Option(..., '--input-price'), output_price: float = typer.Option(..., '--output-price'), max_cost: float = typer.Option(0.25, '--max-cost'), max_output_tokens: int = typer.Option(2048, '--max-output-tokens'), allow_remote: bool = typer.Option(False, '--allow-remote'), approve_writes: bool = typer.Option(False, '--approve-writes'), allow_docker: bool = typer.Option(False, '--allow-docker'), image: str = typer.Option('', '--image'), test_cmd: list[str] = typer.Option([], '--test-cmd')) -> None
```

Ask one explicitly authorized provider for a small patch and test it.

The model receives only files selected with --target. Provider connections
are opt-in and cost-bounded; its diff is checked by immutable host policy.

### `code_export`

```python
code_export(run_id: str, output: Path = typer.Option(..., '--output'), acknowledge_unverified: bool = typer.Option(False, '--acknowledge-unverified')) -> None
```

Export a SHA-256-verified patch without applying it to original source.

### `code_pending`

```python
code_pending() -> None
```

Show interrupted worktrees requiring cleanup, never retry automatically.

### `code_cleanup`

```python
code_cleanup(run_id: str, approve_cleanup: bool = typer.Option(False, '--approve-cleanup')) -> None
```

Explicitly remove a recorded disposable worktree after review.

### `code_repair_status`

```python
code_repair_status(session_id: str) -> None
```

Inspect durable repair checkpoints without exposing prompts or code.

### `code_repair_mock`

```python
code_repair_mock(task: str, contract: Path = typer.Option(..., '--contract'), key: str = typer.Option(..., '--key'), patch_file: list[Path] = typer.Option([], '--patch-file'), test_exit: list[int] = typer.Option([], '--test-exit'), path: Path = typer.Option(Path('.'), '--path'), approve_writes: bool = typer.Option(False, '--approve-writes'), resume: bool = typer.Option(False, '--resume')) -> None
```

Rehearse a bounded repair loop without LLM calls or real test execution.

Every attempt applies a separate operator-provided patch in an isolated
worktree; mock success never certifies real tests or whole-task completion.

### `code_repair_provider`

```python
code_repair_provider(task: str, contract: Path = typer.Option(..., '--contract'), key: str = typer.Option(..., '--key'), path: Path = typer.Option(Path('.'), '--path'), provider: str = typer.Option('openai', '--provider'), model: str = typer.Option(..., '--model'), source_target: list[str] = typer.Option([], '--source-target'), input_price: float = typer.Option(..., '--input-price'), output_price: float = typer.Option(..., '--output-price'), max_cost: float = typer.Option(..., '--max-cost'), max_attempts: int = typer.Option(2, '--max-attempts'), image: str = typer.Option(..., '--image'), allow_remote: bool = typer.Option(False, '--allow-remote'), allow_docker: bool = typer.Option(False, '--allow-docker'), approve_writes: bool = typer.Option(False, '--approve-writes'), resume: bool = typer.Option(False, '--resume')) -> None
```

Run a paid, budgeted patch-repair loop with real Docker verification.

Model transport and Docker execution are both explicitly gated. The
caller-supplied contract controls paths and tests; model output cannot
choose a shell command or authorize any writes.

### `code_native_propose`

```python
code_native_propose(task: str, contract: Path = typer.Option(..., '--contract'), harness: str = typer.Option(..., '--harness'), model: str = typer.Option(..., '--model'), path: Path = typer.Option(Path('.'), '--path'), image: str = typer.Option(..., '--image'), source_target: list[str] = typer.Option([], '--source-target'), max_budget_usd: float = typer.Option(0.5, '--max-budget-usd'), allow_native: bool = typer.Option(False, '--allow-native'), allow_docker: bool = typer.Option(False, '--allow-docker'), approve_writes: bool = typer.Option(False, '--approve-writes'), acknowledge_unmetered: bool = typer.Option(False, '--acknowledge-unmetered')) -> None
```

Optionally use native Claude/Codex CLI as a *proposal-only* worker.

Codex CLI costs cannot be strongly capped in this adapter. Its use
requires a separate acknowledgement; both adapters are unverified with
real installed hosts until user-run connected tests are complete.

### `client_pair`

```python
client_pair(show_token: bool = typer.Option(False, '--show-token')) -> None
```

Generate a user-local pairing secret; reveal only with explicit consent.

Never copy the pairing token into repository files or issue tracker logs.
The extension stores it using VS Code SecretStorage, not workspace settings.

### `client_capabilities`

```python
client_capabilities() -> None
```

Print the versioned local client sync contract without a daemon.

### `client_sessions`

```python
client_sessions(path: Path = typer.Option(Path('.'), '--path'), limit: int = typer.Option(25, '--limit'), before: int | None = typer.Option(None, '--before')) -> None
```

List local workflows for the initialized repository, with stable paging.

### `client_inspect`

```python
client_inspect(session_id: str, path: Path = typer.Option(Path('.'), '--path')) -> None
```

Inspect the same session snapshot as the browser or VS Code client.

### `client_changes`

```python
client_changes(path: Path = typer.Option(Path('.'), '--path'), after: int = typer.Option(0, '--after'), limit: int = typer.Option(50, '--limit')) -> None
```

Continue an ordered metadata-only change feed after the saved cursor.

### `workflow_validate`

```python
workflow_validate(file: Path = typer.Option(..., '--file')) -> None
```

Validate a versioned DAG and display its immutable manifest fingerprint.

### `workflow_submit`

```python
workflow_submit(file: Path = typer.Option(..., '--file'), path: Path = typer.Option(Path('.'), '--path'), idempotency_key: str | None = typer.Option(None, '--idempotency-key')) -> None
```

Register a local workflow; do not start paid calls, code writes or workers.

### `workflow_run`

```python
workflow_run(workflow_id: str, lease_seconds: int = typer.Option(120, '--lease-seconds'), max_wall_seconds: int = typer.Option(120, '--max-wall-seconds')) -> None
```

Dispatch registered offline preparation and synthetic test workers only.

Code-writing workers require separate explicit dependency injection by the
SDK and cannot be enabled through this convenience command.

### `workflow_leader_status`

```python
workflow_leader_status() -> None
```

Read the local background coordinator health without exposing tokens.

### `workflow_serve`

```python
workflow_serve(path: Path = typer.Option(Path('.'), '--path', help='Existing initialized project'), once: bool = typer.Option(False, '--once', help='Perform one scheduling cycle and exit'), max_seconds: float | None = typer.Option(None, '--max-seconds', help='Bound service scheduling lifetime'), poll_seconds: float = typer.Option(2.0, '--poll-seconds'), max_rss_mib: int = typer.Option(512, '--max-rss-mib'), allow_mock: bool = typer.Option(False, '--allow-mock', help='Explicitly enable synthetic mock stages'), low_resource: bool = typer.Option(False, '--low-resource', help='One task at a time, conservative memory and idle polling'), contained: bool = typer.Option(False, '--contained', help='Run preparation in a killable child process with a deadline')) -> None
```

Run an opt-in, restartable, single-device workflow coordinator.

Only offline preparation workers are enabled by default. Agent/provider
execution, shell, Docker, and coding writes require separately injected
consented SDK workers. Install this command under a supervised OS service
for operation over days; a chat session does not launch background jobs.

With ``--contained`` each preparation runs in a child process that is killed
at its deadline (120 seconds from spawn) and on cancel. After a coordinator
crash the next coordinator kills what the old one left behind. On a host
without strong containment every task is refused and blocked. The command
stops with an error when the child cannot import the installed package.

### `workflow_reap_orphans`

```python
workflow_reap_orphans(term_grace_seconds: float = typer.Option(3.0, '--term-grace-seconds', min=0, max=60, help='Seconds between SIGTERM and SIGKILL')) -> None
```

Kill contained worker trees left behind by a dead coordinator.

Takes the exclusive coordinator lease for the duration, so it cannot race a
running coordinator. Only processes whose recorded start token still matches
are signalled. Prints counts only.

### `workflow_supervised_code`

```python
workflow_supervised_code(task: str, contract: Path = typer.Option(..., '--contract'), patch_file: list[Path] = typer.Option([], '--patch-file'), path: Path = typer.Option(Path('.'), '--path'), key: str = typer.Option(..., '--key'), source_target: list[str] = typer.Option([], '--source-target'), mode: str = typer.Option('mock', '--mode'), docker_image: str | None = typer.Option(None, '--docker-image'), allow_docker: bool = typer.Option(False, '--allow-docker'), approve_writes: bool = typer.Option(False, '--approve-writes'), require_red_test: bool = typer.Option(False, '--require-red-test')) -> None
```

Execute a fully journalled prepare/code/review DAG under operator approval.

Offline `mock` exercises state transitions without claiming real tests.
The `docker` mode requires an approved, pre-pulled image and explicit
Docker permission. No patch is merged, committed, pushed or exported.

### `workflow_status`

```python
workflow_status(workflow_id: str) -> None
```

Inspect one workflow and its source-free task states.

### `workflow_attest`

```python
workflow_attest(workflow_id: str) -> None
```

Recheck current private patch integrity without executing any task.

### `workflow_events`

```python
workflow_events(workflow_id: str) -> None
```

Read durable orchestration receipts, never raw task instructions.

### `workflow_reconcile`

```python
workflow_reconcile(workflow_id: str) -> None
```

Reconcile expired leases without replaying uncertain writes.

### `workflow_cancel`

```python
workflow_cancel(workflow_id: str) -> None
```

Revoke future work; does not terminate an already running child process.

### `review_inspect`

```python
review_inspect(workflow_id: str, show_diff: bool = typer.Option(False, '--show-diff')) -> None
```

Inspect a sealed coding artifact; source text is opt-in, never logged.

### `review_approve`

```python
review_approve(workflow_id: str, fingerprint: str = typer.Option(..., '--fingerprint'), revision: int = typer.Option(0, '--revision'), acknowledge_unverified: bool = typer.Option(False, '--acknowledge-unverified'), confirm: bool = typer.Option(False, '--confirm-export-approval')) -> None
```

Approve ONLY export of exactly these patch bytes; never merges or pushes.

### `review_reject`

```python
review_reject(workflow_id: str, fingerprint: str = typer.Option(..., '--fingerprint'), revision: int = typer.Option(0, '--revision')) -> None
```

Reject the currently inspected patch; no approval is granted.

### `review_revoke`

```python
review_revoke(workflow_id: str, fingerprint: str = typer.Option(..., '--fingerprint'), revision: int = typer.Option(..., '--revision')) -> None
```

Revoke a previously granted local export approval.

### `review_history`

```python
review_history(workflow_id: str) -> None
```

List metadata-only approval, rejection, revocation and export events.

### `review_export`

```python
review_export(workflow_id: str, destination: Path = typer.Option(..., '--output'), fingerprint: str = typer.Option(..., '--fingerprint'), revision: int = typer.Option(..., '--revision')) -> None
```

Export an approved patch outside the checkout, with no auto-application.

### `release_verify`

```python
release_verify(manifest: Path = typer.Option(..., '--manifest'), signature: Path = typer.Option(..., '--signature'), public_key: Path = typer.Option(..., '--trusted-public-key'), artifacts_dir: Path = typer.Option(..., '--artifacts-dir'), installed_version: str = typer.Option(..., '--installed-version'), minimum_accepted: str | None = typer.Option(None, '--minimum-accepted-version')) -> None
```

Verify a pinned Ed25519 release; never download, install or execute it.

The trusted public key must be provisioned independently of untrusted
release metadata. Signature validity is necessary, not sufficient, for
a safe automated rollout (which this command does not implement).

### `state_check`

```python
state_check(full: bool = typer.Option(False, '--full', help='Thorough SQLite integrity check'), database: Path | None = typer.Option(None, '--database', help='Explicit database file; defaults to local user state')) -> None
```

Inspect schema, FK integrity and event counts without mutating state.

A failed check exits nonzero; it does not attempt automatic recovery.

### `state_migrate`

```python
state_migrate(source: Path = typer.Option(..., '--source', help='Existing offline legacy SQLite file'), output: Path = typer.Option(..., '--output', help='New upgraded copy; must not exist')) -> None
```

Prepare a separate verified v1→v2 state copy; never mutate active data.

Stop older DecisionBound processes before switching to the new copy.
No files are overwritten and this command never activates the migration.

### `state_backup`

```python
state_backup(output: Path = typer.Option(..., '--output', help='New, private snapshot filename')) -> None
```

Create a consistent online snapshot; never overwrite existing backups.

The snapshot contains sensitive local data and must not be committed to Git.

### `state_restore`

```python
state_restore(backup: Path = typer.Option(..., '--backup', help='Validated snapshot filename'), destination: Path = typer.Option(..., '--destination', help='New recovered DB file, not active state')) -> None
```

Recover to a *new* file only; never replace a running live database.

Manual migration/reconciliation into active state requires a separate,
operator-controlled procedure with the runtime stopped.

### `main`

```python
main() -> None
```

Entry point declared by the Python distribution.

## `decisionbound.web`

Optional authenticated, loopback-only control API for shared local clients.

The web layer is intentionally thin: CLI and browser use exactly the same
services. There is no login SaaS, cloud synchronization or external API by
default. A non-loopback client cannot gain access by merely knowing a project ID.

### `TaskRequest`

Validated input for safe offline task preparation.

### `LocalExportReviewRequest`

Local-user approval bound to an exact review fingerprint and revision.

### `create_app`

```python
create_app() -> FastAPI
```

Build a fresh API instance for tests or a local runtime process.

## `decisionbound.code_models`

Immutable contracts for isolated coding runs and their evidence.

These values are transport-independent. A generated patch is a proposal, not
permission to edit a checkout or proof that the requested task was completed.

### `PatchProposal`

Unified Git patch plus a short, untrusted explanation from a worker.

The patch may contain proprietary source code; durable event logs store only
hashes and counts. Use the private artifact store to persist its content.

### `PatchInspection`

Host-validated patch scope before a write is authorized.

### `Workspace`

One disposable, detached Git worktree for a single run.

### `TestOutcome`

Receipt returned by a restricted test executor, never by the model.

``kind`` distinguishes a real isolated process from a deterministic mock.
A mock may validate control flow, but cannot establish real test success.

#### `TestOutcome.to_dict`

```python
to_dict() -> dict
```

Serialize a test receipt without storing raw test output.

### `CodingOutcome`

Stable receipt for a bounded patch attempt.

``acceptance_verified`` is true only after host-side evidence and a real
isolated test runner pass. ``task_completed`` remains false until stronger
task-specific semantic acceptance or human approval is provided.

#### `CodingOutcome.to_dict`

```python
to_dict() -> dict
```

Return a JSON-compatible, versioned run receipt for clients.

## `decisionbound.code_ports`

Extension points for coding workers, test execution, and verification.

Subclass these interfaces rather than replacing security controls. Regardless
of override implementation, the host retains authority over Git path checks,
write approval, durable trace receipts and completion labels.

### `PatchProposer`

Supply a candidate Git patch without performing any filesystem writes.

#### `PatchProposer.propose`

```python
propose(task: str, source_files: dict[str, str]) -> PatchProposal
```

Return a candidate patch; source is bounded and selected by the caller.

### `IsolatedTestRunner`

Execute a test command inside a disposable workspace.

Implementations must honestly report their isolation kind; only a real
isolated runner can contribute to independent acceptance verification.

#### `IsolatedTestRunner.run`

```python
run(workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome
```

Produce a host-recorded test receipt without including raw source.

### `CodingVerifier`

Independently assess patch and test receipts after agent execution.

#### `CodingVerifier.verify`

```python
verify(run_id: str, inspection: PatchInspection, diff_sha256: str, tests: tuple[TestOutcome, ...]) -> tuple[bool, str]
```

Return acceptance and explanation, never trust a worker's claim.

## `decisionbound.code_workspace`

Durable, disposable Git worktrees for bounded coding tasks.

All mutations are made in detached worktrees under the user's DecisionBound
state directory. This module neither merges branches nor modifies the original
checkout's tracked files. Interrupted runs leave records for manual cleanup.

### `WorkspaceError`

A project cannot be safely isolated or an owned worktree cannot be cleaned.

### `GitCommandError`

The Git executable was missing, timed out or exited with an error.

This separates "Git could not answer" from "Git answered and the checkout
is not acceptable". The message never carries paths or Git's stderr.

### `worktree_git_dir`

```python
worktree_git_dir(workspace: Workspace) -> Path
```

Find the Git metadata directory of a managed worktree without reading anything the worktree holds.

The answer comes only from the source repository's own ``worktrees/*/gitdir`` files, which the
container cannot reach. Exactly one entry must point back at this worktree.

### `git_entry_is_original`

```python
git_entry_is_original(workspace: Workspace, git_dir: Path) -> bool
```

True only while ``<worktree>/.git`` is still the regular pointer file Git wrote.

Test code can replace it with a directory or a symlink. Nothing runs from it once the host passes
``--git-dir``, but a replaced entry means the worktree was tampered with and the run is not trusted.

### `GitWorktreeManager`

Own isolated worktrees and retain resumable cleanup metadata.

Args:
    state_root: Private writable root for records and temporary worktrees.

Safety:
    Only clean Git repositories are accepted. No shell evaluation, remote
    Git fetch, commit, push, merge or repository hook is performed. Custom
    configured Git filters are refused to avoid checkout-time executables.
    A Git worktree is *not* a process sandbox: untrusted test code must run
    through a separate isolation backend.

#### `GitWorktreeManager.check_source`

```python
check_source(repository_root: Path) -> tuple[Path, str]
```

Validate a clean Git root and return the immutable base commit.

#### `GitWorktreeManager.create`

```python
create(repository_root: Path, run_id: str) -> Workspace
```

Create one detached worktree and persist a recovery record.

#### `GitWorktreeManager.reconcile`

```python
reconcile(run_id: str) -> Workspace | None
```

Return an interrupted run's workspace without creating new state.

#### `GitWorktreeManager.cleanup`

```python
cleanup(run_id: str) -> bool
```

Remove only a registered, managed worktree and mark its record closed.

Cleanup is idempotent. If validation fails, leave the workspace intact
for manual investigation rather than recursively deleting arbitrary
paths. This may remove uncommitted edits in *the isolated worktree*.

#### `GitWorktreeManager.pending`

```python
pending() -> list[str]
```

List runs with durable cleanup records not yet marked closed.

## `decisionbound.code_patches`

Fail-closed validation and application of small, text-only Git patches.

This is intentionally narrower than `git apply`: no deletions, executable
changes, symlinks, binary patches, path traversal, rename/copy, or protected
agent/CI/credential files. Review cannot be bypassed by a custom proposer.

### `UnsafePatch`

A proposed patch violates the host's immutable safety policy.

### `PatchPolicy`

Limits for text-only patches to regular, non-protected repository files.

Custom callers can further restrict ``allowed_globs`` and limits, but
cannot remove the built-in sensitive-file protections.

#### `PatchPolicy.check_path`

```python
check_path(workspace: Path, raw: str) -> None
```

Reject traversal, symlinks (including ancestors), and protected files.

### `PatchValidator`

Validate a patch before Git is allowed to touch an isolated checkout.

#### `PatchValidator.inspect`

```python
inspect(patch: str, workspace: Path) -> PatchInspection
```

Validate format, paths, modes and limits, returning scope receipts.

A second independent ``git apply --check`` must succeed before writing.
This parser deliberately rejects many valid Git diff features.

#### `PatchValidator.check_git_modes`

```python
check_git_modes(workspace: Path, paths: tuple[str, ...], git_dir: Path | None = None) -> None
```

Block Git symlinks/submodules and non-regular tracked file modes.

#### `PatchValidator.apply`

```python
apply(patch: str, workspace: Path, git_dir: Path | None = None) -> PatchInspection
```

Apply a validated patch to one already-created disposable worktree.

Calls Git only with fixed executable arguments and stdin patch bytes;
never interpolates model-controlled text into a shell command.

## `decisionbound.code_sandbox`

Pluggable test executors with explicit trust labels and isolation boundaries.

The default test executor is an inert deterministic mock for CI and research.
Real execution requires an explicitly authorized Docker-compatible daemon:
no network, no privileges, explicit CPU/memory/PID and timeout ceilings.

### `ScriptedTestRunner`

Deterministic test fake; does not execute or certify user code.

Args:
    checker: Optional function to inspect the disposable workspace and
        return a fake exit code. It is test infrastructure, not sandboxing.

#### `ScriptedTestRunner.run`

```python
run(workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome
```

Return a clearly labeled mock receipt for deterministic tests.

### `DockerTestRunner`

Run untrusted project tests in a constrained, network-disabled container.

This backend assumes the operator trusts the local Docker daemon and its
image provenance. Docker itself must be installed/configured by the user;
container escape resistance is not independently certified by this code.

By design the container has no mounted home, secrets, SSH agent or Docker
socket; only the isolated worktree is mounted read/write. It may mutate
that worktree, which the verifier detects after execution.
A root caller is refused before contacting the daemon. On platforms without
POSIX user IDs the container uses the fixed unprivileged ID 1000:1000.

#### `DockerTestRunner.run`

```python
run(workspace: Workspace, command: tuple[str, ...], timeout_seconds: int) -> TestOutcome
```

Run a command with kernel-enforced limits; never use host shell.

The caller must explicitly opt in to real container execution. Output
is hashed as it arrives rather than stored or copied into durable
execution logs. A run that prints more than OUTPUT_HARD_CAP_BYTES is
stopped and its container removed. Users can inspect test errors
separately through a controlled local workflow.

## `decisionbound.code_execution`

Bounded patch execution in disposable worktrees with independent receipts.

A patch proposer may be a language model, human-supplied patch, or mock; none
of them can authorize writes, weaken the patch policy, or certify completion.
Only an explicit caller approval enables writes, and untrusted tests require
an external sandbox backend. The original checkout is never modified.

### `PatchFileProposer`

Use an operator-supplied patch exactly as supplied, without model calls.

#### `PatchFileProposer.propose`

```python
propose(task: str, source_files: dict[str, str]) -> PatchProposal
```

Return the candidate patch; the host still validates every byte.

### `CodeReceiptVerifier`

Reconcile host-side Git/test events, rejecting fake worker evidence.

A passing result certifies only that the patch was applied as authorized,
the resulting code did not change unexpectedly during tests, and all real
isolated tests passed. It does *not* certify task semantics or deployment.

#### `CodeReceiptVerifier.verify`

```python
verify(run_id: str, inspection: PatchInspection, diff_sha256: str, tests: tuple[TestOutcome, ...]) -> tuple[bool, str]
```

Verify immutable SHA-256 receipts and real isolation proof labels.

### `run_coding_task`

```python
run_coding_task(*, project_path: Path, state_root: Path, repository: SQLiteRepository, task: str, proposer: PatchProposer, runner: IsolatedTestRunner, test_commands: tuple[tuple[str, ...], ...], approved_writes: bool = False, patch_policy: PatchPolicy | None = None, source_targets: tuple[str, ...] = (), timeout_seconds: int = 120, verifier: CodingVerifier | None = None, contract: TaskContract | None = None, run_id: str | None = None, require_red_test: bool = False) -> CodingOutcome
```

Execute one bounded patch proposal in an isolated worktree.

Procedure: authenticated project → explicit write consent → clean commit →
disposable worktree → strict patch preflight → apply → artifact and receipts
→ isolated tests → unchanged-diff check → independent verification → cleanup.

Raises:
    PermissionError: Writes were not explicitly authorized.
    UnsafePatch: The patch violates host safety or escapes its approved scope.
    WorkspaceError: Git isolation or reconciliation failed.

The caller must configure a separate safe sandbox for untrusted tests.
This API never merges, commits, pushes, or changes the original checkout.
All failure paths append metadata-only events before re-raising.

## `decisionbound.code_provider`

Budgeted, schema-bound LLM patch proposer with no write authority.

This adapter returns a candidate diff; it never invokes Git or tools. The
immutable host patch policy decides which edits may be applied and the sandbox
runner/verification service decide what evidence counts as a passing test.

### `ModelPatchProposer`

Request exactly one JSON-formatted Git patch under explicit cost limits.

The host supplies explicit target-file text; the model cannot browse the
whole repository or access unrelated files through this adapter. Network
permission is enforced by the configured provider transport, independently
from the caller's explicit write consent at coding-run time.

#### `ModelPatchProposer.propose`

```python
propose(task: str, source_files: dict[str, str]) -> PatchProposal
```

Return a candidate diff, rejecting malformed outputs and cost overrun.

No retry follows an ambiguous provider error. This proposer is single
use; callers must construct a new instance for a new task.

## `decisionbound.code_artifacts`

Human-controlled export of local, integrity-checked coding artifacts.

A patch is *never* automatically merged, committed, applied to the original
checkout, published, or pushed by this module. Consumers must explicitly
review and promote it through their own Git workflow.

### `ArtifactVerificationError`

A private patch no longer matches the host's original run receipt.

### `export_coding_patch`

```python
export_coding_patch(*, repository: SQLiteRepository, state_root: Path, run_id: str, destination: Path, acknowledge_unverified: bool = False) -> dict
```

Safely export a reviewed Git patch with its recorded provenance.

The operation verifies that the run reached a terminal state, the patch
receipt is unique, and saved bytes still match its SHA-256 digest. Failed
or mock runs require an explicit acknowledgment before export. Destination
creation is exclusive to avoid overwriting any existing user file.

## `decisionbound.code_acceptance`

Operator-authored, deterministic acceptance contracts for coding runs.

Contracts are *not* generated by a coding agent and do not grant authority to
edit a repository. They define independently observable constraints: permitted
and required changed files, regression commands, and bounded text predicates.
A passing contract certifies only those checks, not broad semantic correctness.

### `TextPredicate`

A host-evaluated text condition on an approved, nonsecret source file.

``contains`` and ``excludes`` are literal substrings, not executable regex
or Python expressions. File contents are never copied into durable logs.

### `TaskContract`

Caller-defined, versioned acceptance scope for a single coding task.

``allowed_paths`` is a hard upper bound on changed paths; ``required_paths``
is a subset that *must* change. ``test_commands`` are exact argv sequences
chosen by the operator, never inferred from model output. Only a real,
host-controlled sandbox may certify their successful execution.

An explicit contract cannot authorize protected paths. All supplied paths
are validated again against the immutable host patch policy at execution.

#### `TaskContract.to_dict`

```python
to_dict() -> dict[str, Any]
```

Export a nonsecret, stable, JSON-compatible contract.

#### `TaskContract.from_dict`

```python
from_dict(cls, obj: dict[str, Any]) -> 'TaskContract'
```

Reject unknown fields and invalid nested assertions before execution.

#### `TaskContract.sha256`

```python
sha256() -> str
```

Return the fingerprint used to pin contracts across retries/resumes.

#### `TaskContract.preflight`

```python
preflight(workspace: Path, policy: PatchPolicy, test_commands: tuple[tuple[str, ...], ...]) -> None
```

Ensure path policy and test argv cannot be weakened by the model.

#### `TaskContract.evaluate`

```python
evaluate(workspace: Workspace, inspection: PatchInspection) -> tuple[bool, str]
```

Check actual changed-file scope and text predicates after test execution.

Returns a short, source-free failure code; no raw matched text is logged.

## `decisionbound.code_journal`

Transactional, idempotency-keyed local journal for repair sessions.

The SQLite adapter serializes reservations and never retries an in-flight
attempt automatically. A restart can reconstruct a fully recorded attempt,
but if a write/test might still be running it fails closed for operator
reconciliation. This is *single-user local* durability, not distributed leases.

### `RepairConflict`

The session is already running, mismatches its key, or needs review.

### `AttemptEntry`

Public, source-free checkpoint for one reserved coding attempt.

### `RepairState`

Versioned status of a bounded repair session (not proof of completion).

#### `RepairState.to_dict`

```python
to_dict() -> dict[str, Any]
```

Serialize safe journal metadata for CLI, UI and integration clients.

### `SQLiteRepairJournal`

Single-user implementation of crash-safe repair attempt reservations.

Requires a :class:`SQLiteRepository` initialized with schema version 2.
Status transitions use `BEGIN IMMEDIATE` so duplicate callers cannot
reserve two writes for one idempotency key. A parallel daemon should use
a different backend with authenticated durable leases.

#### `SQLiteRepairJournal.open`

```python
open(*, idempotency_key: str, request_sha256: str, project_id: str, contract_sha256: str, max_attempts: int) -> tuple[RepairState, bool]
```

Create a session or retrieve the same immutable request by key.

#### `SQLiteRepairJournal.get`

```python
get(session_id: str) -> RepairState
```

Load checkpoints without returning raw prompts, source, or test output.

#### `SQLiteRepairJournal.reserve`

```python
reserve(session_id: str, *, cost_reservation_usd: float = 0.0) -> AttemptEntry
```

Reserve exactly one new run ID before any external model/Git action.

#### `SQLiteRepairJournal.finish_attempt`

```python
finish_attempt(session_id: str, run_id: str, *, status: str, summary: dict[str, Any], cost_delta: float = 0.0, feedback: dict[str, Any] | None = None) -> RepairState
```

Commit a terminal attempt and its cost in one SQLite transaction.

#### `SQLiteRepairJournal.close`

```python
close(session_id: str, status: str) -> RepairState
```

Mark a session terminal, refusing closure with unaccounted writes.

#### `SQLiteRepairJournal.reconcile`

```python
reconcile(session_id: str) -> RepairState
```

Reconstruct only a *completed and intact* attempt after process loss.

A started but unfinished coding run is never automatically replayed. Its
worktree may contain unreviewed changes or an active sandbox process.
The operator must inspect and clean it before starting another session.

## `decisionbound.code_repair`

Bounded, host-controlled iterative repair with crash-reconstructable state.

Unlike autonomous coding agents, this coordinator does not execute generated
shell commands, reuse patches after ambiguous writes, or treat the agent's
completion claim as evidence. Each attempt uses a fresh isolated Git worktree
and independently recorded test/contract results. Only explicit operator-
authorized writes are allowed, and all attempts remain reviewable patches.

### `RepairLimits`

Hard attempt/cost ceilings and a best-effort wall-clock limit.

Individual provider and sandbox calls also need their own timeouts; this
wall-clock limit is checked *between* attempts and does not kill a provider.

### `RepairFeedback`

Source-free, bounded feedback from independently recorded checks.

Failed test argv, stdout, stderr, raw source, and credentials never enter
this object. A provider may use the short failure category to propose a
*new* patch against the original source snapshot.

#### `RepairFeedback.to_dict`

```python
to_dict() -> dict
```

Serialize only the minimal diagnostics needed for the next attempt.

#### `RepairFeedback.from_dict`

```python
from_dict(cls, value: dict) -> 'RepairFeedback'
```

Reject unrecognized journal feedback before passing it to a model.

### `RepairProposerFactory`

Override to select model/provider or synthesize a patch per attempt.

``make`` must not perform any file changes. Network-capable factories must
enforce request-level budgets before sending paid calls and account for
ambiguous provider failures; the coordinator also enforces a total ceiling.

#### `RepairProposerFactory.make`

```python
make(attempt: int, feedback: RepairFeedback, remaining_cost_usd: float) -> PatchProposer
```

Return a fresh, single-use proposer for the requested attempt.

#### `RepairProposerFactory.requires_budget_reservation`

```python
requires_budget_reservation() -> bool
```

Whether this proposer may send paid model calls (fail-closed default).

#### `RepairProposerFactory.accounted_cost_usd`

```python
accounted_cost_usd() -> float
```

Report cumulative reserved or actual model spending; default zero.

### `ScriptedRepairFactory`

Deterministic mock factory for offline tests, no provider network calls.

#### `ScriptedRepairFactory.requires_budget_reservation`

```python
requires_budget_reservation() -> bool
```

Offline deterministic fixtures need no model budget reservation.

#### `ScriptedRepairFactory.make`

```python
make(attempt: int, feedback: RepairFeedback, remaining_cost_usd: float) -> PatchProposer
```

Select a fixture while retaining provenance of the previous failure.

### `FeedbackPatchProposer`

Add bounded, host-derived retry feedback to an ordinary patch proposer.

#### `FeedbackPatchProposer.propose`

```python
propose(task: str, source_files: dict[str, str])
```

Request a fresh full patch against the *original* source snapshot.

### `ModelRepairFactory`

Optional cost-aware bridge to the existing JSON model transport port.

A new :class:`ModelPatchProposer` is instantiated for each attempt, with
the remaining total spending cap. Unknown provider usage is charged at the
full reserved bound rather than treated as free.

#### `ModelRepairFactory.make`

```python
make(attempt: int, feedback: RepairFeedback, remaining_cost_usd: float) -> PatchProposer
```

Create an individually bounded remote proposer for one paid call.

#### `ModelRepairFactory.accounted_cost_usd`

```python
accounted_cost_usd() -> float
```

Sum reserved/actual expenditures, including ambiguous failed calls.

### `run_repair_session`

```python
run_repair_session(*, idempotency_key: str, project_path: Path, state_root: Path, repository: SQLiteRepository, task: str, contract: TaskContract, proposer_factory: RepairProposerFactory, runner: IsolatedTestRunner, limits: RepairLimits, source_targets: tuple[str, ...] = (), approved_writes: bool = False, timeout_seconds: int = 120, resume: bool = False, patch_policy: PatchPolicy | None = None, require_red_test: bool = False) -> RepairState
```

Execute up to N independent worktrees and checkpoint every attempt.

The caller must authorize workspace writes and provide the independent
acceptance contract. If process death interrupts a reserved attempt,
subsequent calls require ``resume=True`` and reconstruct only fully
recorded, intact previous runs. Incomplete/ambiguous attempts are blocked.
No user checkout is changed and no patch is merged automatically.

A successful *mock* sequence reports ``needs_real_tests``, never verified.
A real sequence can report ``verified`` for the pinned contract, but does
not assert semantic completion of any unstated requirements.

## `decisionbound.code_native`

Optional Claude Code/Codex CLI patch proposers, without agent write authority.

These adapters intentionally ask an installed native harness only for a JSON
patch proposal. They do **not** delegate patch application or test execution to
it. The harness subprocess may read local config or use network credentials:
callers must opt in, trust the installed executable, and use approved accounts.
Codex CLI spend is not bounded by this adapter; do not use it where a hard
USD ceiling is required. These paths are mock-tested, not live verified.

### `NativeCLIError`

A native harness returned malformed, unsafe or incomplete proposal data.

### `CodexCLIPatchProposer`

Use ``codex exec`` in read-only mode to propose a patch as structured JSON.

Requires a separately installed Codex CLI, approved authentication, and
explicit consent to run it. **Read-only CLI flags do not constitute an OS
sandbox**. There is no hard USD cost enforcement in this adapter; callers
must confirm that they accept provider costs and rate limits themselves.

#### `CodexCLIPatchProposer.propose`

```python
propose(task: str, source_files: dict[str, str]) -> PatchProposal
```

Invoke a single read-only, non-interactive Codex proposal attempt.

### `ClaudeCLIPatchProposer`

Use ``claude -p`` with tools/MCP disabled and JSON schema output.

Claude Code configuration may still influence a native process; all
source is supplied explicitly and the process runs in an empty directory.
For a third-party product, use compliant user-provided Anthropic API keys;
this adapter never extracts or intermediates Claude.ai login credentials.

#### `ClaudeCLIPatchProposer.propose`

```python
propose(task: str, source_files: dict[str, str]) -> PatchProposal
```

Request one structured proposal with Claude's own spend cap.

## `decisionbound.code_review`

Independent, fail-closed review of completed coding stages.

This is an *evidence* review, not an LLM opinion or proof of semantic
correctness. A reviewer cannot promote mocked tests or an unchecked patch to a
verified outcome. Its source of truth is the protected host journal and the
private, checksum-validated patch artifact. Project owners can add veto-only
policy hooks, but not bypass canonical verification.

### `ReviewBlocked`

Required host-side evidence was missing, inconsistent, or unverifiable.

### `CodeReviewEvidence`

Minimal review record; never stores source, prompts, or model output.

The digest is tied to a particular code stage, repair session, immutable
patch bytes, contract, verified test receipts, and checked-out base commit.
``task_completed`` intentionally remains false even when this stage passes.

#### `CodeReviewEvidence.sha256`

```python
sha256() -> str
```

Return a stable digest that the host verifier independently rebuilds.

### `CodeReviewPolicy`

Veto-only review extension; it cannot grant permissions or certify tests.

Override this for extra, deterministic checks such as an organization-wide
maximum patch size. Returning ``True`` allows the host to continue its own
safety checks; it does not declare the task complete.

#### `CodeReviewPolicy.accept`

```python
accept(evidence: CodeReviewEvidence) -> bool
```

Return false to block a candidate whose canonical evidence passed.

### `AllowReviewedEvidence`

Default policy: enforce host evidence checks without extra vetoes.

#### `AllowReviewedEvidence.accept`

```python
accept(evidence: CodeReviewEvidence) -> bool
```

Allow host-validated evidence to pass to the independent verifier.

### `CodeReviewService`

Read-only, restart-safe evidence reviewer for an approved coding stage.

Args:
    repository: Owner-controlled state ledger, never a model-generated DB.
    state_root: Private artifact directory containing saved patches.

This service makes no network calls, invokes no model, and never replays a
coding attempt. A real Docker test still needs verification on a connected
host; mock receipts cannot be relabeled by this service.

#### `CodeReviewService.examine`

```python
examine(*, workflow_id: str, project_id: str, project_path: Path, code_task_id: str, expected_receipt_sha256: str) -> CodeReviewEvidence
```

Rebuild the canonical code-review receipt without executing user code.

The upstream task must have been host-verified in the *same* workflow.
A matching code idempotency key associates the repair journal with the
task, so workers cannot submit unrelated successful sessions as proof.

## `decisionbound.supervised_coding`

One-call supervised coding workflow with explicit authorization and review.

The pipeline is `prepare -> implement -> audit`. Each stage is a separate
journalled task. The implementer produces isolated, reviewable artifacts; the
auditor reconstructs host receipts after real tests pass. Even successful
verification means only the operator's acceptance *contract* was met, not
unbounded semantic completion or permission to merge into a repository.

### `SupervisedCodingRequest`

Explicitly approved bounds for a single supervised coding assignment.

The contract is authored by a human/operator, not the coding model. The
proposer is never allowed to write to the repository: only DecisionBound
may apply a validated patch in a disposable Git worktree. Provider API
keys and private file contents must not appear in this request.

#### `SupervisedCodingRequest.workflow`

```python
workflow(*, approval_sha256: str | None = None) -> WorkflowSpec
```

Construct an immutable, three-stage execution and review DAG.

This does not enable tools or perform writes. It binds the budget and
prevents a reviewer from running before real code acceptance is checked.

### `SupervisedCodingOrchestrator`

Operator-owned entrypoint for one sandboxed coding DAG.

This class is deliberately small: it wires already tested host components
and does not invent a second journal, patch validator, or verifier. Its
dependency injection enables offline scripted tests and a real Docker
backend in connected environments.

Args:
    repository: Local protected SQLite store.
    state_root: Private storage for isolated worktrees and artifacts.
    runner: Operator-configured isolated test runner.
    proposer_factory: Operator-configured provider or scripted proposer.
    policy: Optional veto-only review policy.

#### `SupervisedCodingOrchestrator.execute`

```python
execute(*, project_path: Path, request: SupervisedCodingRequest, idempotency_key: str, approved_writes: bool = False, lease_seconds: int = 180, max_wall_seconds: int = 600) -> dict
```

Run one authorized workflow and return a source-free audit summary.

Never commits, merges or pushes. No job runs without explicit write
authorization. Restarting with the same key returns the prior workflow
without replaying completed or ambiguously interrupted coding work.

## `decisionbound.workflows`

Durable local DAG orchestration with leases, budget reservations and evidence gates.

The scheduler operates on metadata and user-approved, immutable task specifications.
Only explicitly registered workers execute tasks.  A mock result can never be
mistaken for independently verified code execution.  SQLite is suitable for
one-machine processes, not distributed consensus or remote multi-tenancy.

### `WorkflowConflict`

A claim, budget, project identity or workflow revision is no longer valid.

### `WorkflowTask`

Bounded task contract; ``kind`` determines the host's verification policy.

``description`` is stored in the local private DB for durable resume; never
include credentials. It is treated as untrusted task input, not a system prompt.
Paid adapters must honor the reserved per-task budget before network calls.

#### `WorkflowTask.from_dict`

```python
from_dict(cls, value: Mapping[str, Any]) -> 'WorkflowTask'
```

Reject unknown keys and malformed fields rather than guessing intent.

#### `WorkflowTask.to_dict`

```python
to_dict() -> dict
```

Export a stable task specification without runtime secrets or leases.

### `WorkflowSpec`

Finite DAG with a shared budget and explicit execution concurrency.

#### `WorkflowSpec.from_dict`

```python
from_dict(cls, value: Mapping[str, Any]) -> 'WorkflowSpec'
```

Parse a fully specified workflow without running any requested action.

#### `WorkflowSpec.to_dict`

```python
to_dict() -> dict
```

Serialize as a versioned, portable workflow manifest.

### `TaskLease`

Claimed task bound to one private owner token and monotonically increasing generation.

#### `TaskLease.assignment`

```python
assignment() -> 'TaskAssignment'
```

Strip claim token before handing work to a potentially untrusted plugin.

### `TaskHandoff`

Small immutable handoff manifest from a verified prerequisite stage.

Only host-origin receipt fingerprints cross the boundary, not another
agent's raw reasoning, entire tool history, workspace or credentials.

### `TaskAssignment`

Least-privileged worker input; no lease owner token or generation.

### `WorkerReceipt`

Worker output, untrusted until independently checked by the host verifier.

``status`` is advisory. Raw model output, secrets, test stdout and source code
must not be persisted here; use private artifacts and independent receipts.

### `ExecutionControl`

Host-side cancellation handle for one running claim.

The supervisor creates one per lease and sets :attr:`cancel` when the claim
is revoked, the deadline of the whole run passes, the stop grace expires or
leadership is lost. It carries claim *references* (workflow, task, attempt
and generation) for evidence records. It never carries the private owner
token, so a worker that receives it still cannot finalize its own task.

#### `ExecutionControl.request_cancel`

```python
request_cancel(reason: str) -> None
```

Ask the worker to stop. The first reason given is kept.

### `WorkflowWorker`

Overridable per-kind worker; cannot claim or finalize tasks directly.

Workers that run in a supervisor thread cannot be forcibly stopped. Only a
worker that overrides :meth:`execute_controlled` and owns a killable
process tree, such as ``ContainedWorker``, can honor cancellation.

#### `WorkflowWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Perform a bounded task and return a source-free completion receipt.

#### `WorkflowWorker.execute_controlled`

```python
execute_controlled(assignment: TaskAssignment, control: ExecutionControl) -> WorkerReceipt
```

Perform the task while honoring ``control``; defaults to :meth:`execute`.

The default ignores ``control``, so existing in-process workers keep
their documented behavior: they are not killable.

### `PreparationWorker`

Offline, read-only task preparation using the existing context engine.

#### `PreparationWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Generate a task capsule; no remote model, shell or network needed.

### `ModelInspectionWorker`

Bounded model-driven read-only task worker from Sprint 03.

The caller injects a fresh JSON transport factory and explicit USD pricing.
No default network is made and paid transports require reserved budgets.
Each task writes a separate read-only event ledger whose host evidence is
checked again by :class:`IndependentTaskVerifier` before handoff.

#### `ModelInspectionWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Run the isolated model/tool loop under this task's spend reservation.

### `ScriptedWorkflowWorker`

Offline deterministic fake for testing dispatch; never verifies real work.

#### `ScriptedWorkflowWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Return an explicitly synthetic outcome after an optional test delay.

### `CodingRepairWorker`

Adapter to Sprint 05's bounded patch-repair engine (disabled by default).

All contracts, proposers and isolated test runners are injected by the
operator. This adapter does not grant write permission, merge patches or
inherit network credentials from the supervisor.

#### `CodingRepairWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Execute only an explicitly consented coding task via run_repair_session.

### `CodeEvidenceReviewWorker`

Evidence-only reviewer for one verified coding predecessor.

This worker does not ask an LLM whether the patch is correct. It reconstructs
host receipts, validates the private patch bytes, confirms sandbox tests,
and checks that the original source commit is unchanged. An optional custom
:class:`CodeReviewPolicy` may veto but cannot certify a missing test.

#### `CodeEvidenceReviewWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Return a digest of independently reconstructed real test evidence.

### `IndependentTaskVerifier`

Classify stage receipts from host state, never from a model's success claim.

A verified *preparation stage* is not a completed user request. For coding,
the Sprint 05 repair journal must independently report approved real tests.
Mock outcomes are always labeled non-verifying.

#### `IndependentTaskVerifier.verify`

```python
verify(lease: TaskLease, receipt: WorkerReceipt) -> str
```

Return the only permitted final state for a claimed worker result.

### `WorkflowJournal`

SQLite-backed task ledger with CAS leases and durable budget reservations.

Atomic ``BEGIN IMMEDIATE`` claims prevent duplicate scheduling by competing
local processes. Expired read-only claims can be retried; expired code claims
are quarantined because a write may have completed despite the timeout.
Leases are not an execution sandbox. A lease alone cannot stop a process:
a ``ContainedWorker`` kills its process tree when the supervisor sees the
claim revoked, but in-process thread workers cannot be stopped.

#### `WorkflowJournal.create`

```python
create(*, project_path: Path, spec: WorkflowSpec, idempotency_key: str) -> str
```

Register an immutable graph exactly once for one initialized project.

#### `WorkflowJournal.list_active`

```python
list_active(*, project_id: str, limit: int = 50, after: str | None = None) -> list[str]
```

List only a project's unfinished graphs using a bounded fair cursor.

``after`` is a previously observed workflow ID. Keyset ordering is by
``(created_at,workflow_id)``, and selection wraps at the end. No global
unbounded workflow scan is required as the local history grows.
Callers must already resolve the project's trusted local identity.

#### `WorkflowJournal.claim`

```python
claim(workflow_id: str, *, worker_kinds: set[str], lease_seconds: int = 120, now: float | None = None, worker_routes: set[str] | None = None) -> TaskLease | None
```

Atomically claim one ready task under concurrency and dollar ceilings.

No write task is ever retried by this dispatcher. Model-capable adapters
must be cost-bounded by their own provider transport as well.

#### `WorkflowJournal.heartbeat`

```python
heartbeat(lease: TaskLease, *, lease_seconds: int = 120) -> None
```

Renew a currently owned lease; stale generations cannot extend it.

#### `WorkflowJournal.finish`

```python
finish(lease: TaskLease, *, state: str, receipt: WorkerReceipt, now: float | None = None, verifier: IndependentTaskVerifier | None = None) -> None
```

CAS-complete only if the canonical host evidence gate agrees.

A plugin or caller cannot upgrade ``mock_passed`` to ``verified`` by
directly invoking the journal. Custom verifier implementations may
tighten a result but never relax the canonical safety gate.

#### `WorkflowJournal.quarantine`

```python
quarantine(lease: TaskLease, *, reason: str = 'untrusted_result') -> None
```

Mark an uncertain or over-budget claim terminal without retrying it.

#### `WorkflowJournal.claim_is_current`

```python
claim_is_current(lease: TaskLease, *, now: float | None = None) -> bool
```

Read-only check that ``lease`` still owns its task (token, generation, unexpired).

#### `WorkflowJournal.note_late_result`

```python
note_late_result(lease: TaskLease, reason: str) -> None
```

Record, as an event only, that a result or failure arrived after the claim was lost.

No task state changes. The compare-and-swap in :meth:`finish` and
:meth:`fail` is what rejects the late result. This adds the evidence.

#### `WorkflowJournal.record_containment_event`

```python
record_containment_event(workflow_id: str, task_id: str | None, event: str, metadata: dict | None = None) -> None
```

Append one allowlisted containment event (no task state changes).

Only fixed event names are accepted so that process evidence cannot
impersonate scheduler transitions such as ``claimed`` or ``finished``.

#### `WorkflowJournal.block_uncontained`

```python
block_uncontained(workflow_id: str, task_id: str, generation: int) -> bool
```

Block a task that was leased or pending at exactly ``generation``.

Used when a worker process could not be proven dead. Returns False and
changes nothing when the task already moved on to another generation or
a terminal state, so a newer claim is never disturbed.

#### `WorkflowJournal.fail`

```python
fail(lease: TaskLease, exc: Exception, *, now: float | None = None) -> None
```

Fail safely. Only read-only tasks may retry after a definite exception.

#### `WorkflowJournal.reconcile`

```python
reconcile(workflow_id: str, *, now: float | None = None) -> dict
```

Reconcile stale tasks without running them or trusting agent output.

#### `WorkflowJournal.cancel`

```python
cancel(workflow_id: str) -> dict
```

Revoke all task claims.

A supervisor notices the revoked claim at its next heartbeat tick and
kills the process tree of a ``ContainedWorker``, even when the cancel
came from another process. In-process thread workers and Docker coding
sandboxes are not stopped, so operators must terminate and inspect
those separately. Late completions fail the owner-token CAS check.

#### `WorkflowJournal.status`

```python
status(workflow_id: str) -> dict
```

Return a safe, source-free view for CLI, web and other clients.

#### `WorkflowJournal.attest`

```python
attest(workflow_id: str, *, state_root: Path) -> dict
```

Re-check completed code-review evidence *at inspection time*.

The original append-only workflow result is not silently rewritten.
This is a separate, read-only current-integrity check; it detects
patch tampering or source HEAD changes after earlier verification.
It does not certify the user's broad task or promise that files will
remain unchanged after the attestation completes.

#### `WorkflowJournal.events`

```python
events(workflow_id: str) -> list[dict]
```

Read only bounded, metadata-only workflow transition evidence.

### `WorkflowSupervisor`

Bounded concurrent dispatcher with injected workers and independent checks.

No implicit network, shell, Docker or write permissions. Two local
supervisors can poll concurrently; SQLite serializes claims. Worker-facing
leases are opaque and finalization is owner/generation checked. Thread
concurrency is local-only and does not imply distributed worker support.

Every lease gets an :class:`ExecutionControl`. While any worker runs, the
supervisor ticks every ``min(0.5, lease_seconds/3)`` seconds: it renews the
lease, and a failed renewal (claim cancelled or replaced) cancels that
worker. Only workers that implement ``execute_controlled`` with a killable
process, such as ``ContainedWorker``, actually stop.

#### `WorkflowSupervisor.run`

```python
run(workflow_id: str, *, lease_seconds: int = 120, max_wall_seconds: int = 120, max_dispatch: int = 100, stop_event: threading.Event | None = None, abort_event: threading.Event | None = None, stop_grace_seconds: float = 10) -> dict
```

Schedule eligible tasks and wait for local workers within soft limits.

``max_wall_seconds`` is a best-effort dispatch limit, not a hard process
or sandbox timeout. Per-worker provider and test timeouts are required.
``stop_event`` stops new dispatch and, after ``stop_grace_seconds``,
cancels running workers. ``abort_event`` (leadership lost) cancels them
at the next tick. Cancelling only has an effect on killable workers.

## `decisionbound.containment`

Worker-process containment: hard deadlines, tree termination and orphan cleanup.

A :class:`ContainedWorker` runs each task in a real child process that the host
can kill, together with every descendant that stayed in the child's process
group (POSIX) or job object (Windows). Deadlines, cancellation, leadership loss
and output floods end in a confirmed termination, or in an explicit
``ContainmentFailure`` that the journal turns into a blocked task.

What this is not: a security sandbox. Descendants that call ``setsid`` or
``setpgid`` escape a POSIX group. Windows processes that break away from the job
or start through WMI or a service escape the job. There is no filesystem,
network, credential or syscall isolation. Use the Docker coding sandbox for
that. See ``site_docs/worker-containment.md`` for the full list.

Only the Linux code paths of this module were executed by its authors. The
macOS and Windows paths follow the platform documentation and have not been
verified. A passing CI matrix would be the first evidence, not this file.

### `ContainmentError`

Base class for failures of worker-process containment.

### `WorkerDeadlineExceeded`

The worker ran past its hard deadline and its process tree was killed.

### `WorkerCancelled`

The host cancelled the worker (revoked claim, stop, or lost leadership).

### `WorkerCrashed`

The worker exited abnormally, or could not be started.

### `WorkerOutputInvalid`

The worker's output was missing, malformed or larger than allowed.

### `ContainmentFailure`

Containment was refused, or termination of the process tree could not be confirmed.

### `ResourceLimits`

Optional per-worker resource ceilings. ``None`` means not requested.

``max_output_bytes`` is always enforced by the host while reading the
worker's single receipt line. The other limits are applied by the operating
system. A requested limit that this host cannot enforce makes a strong
containment request fail closed instead of silently running unlimited.

#### `ResourceLimits.requested`

```python
requested() -> frozenset[str]
```

Names of the optional limits that were actually requested.

### `ContainmentCapabilities`

What this host can guarantee: a containment ``level`` and enforced limit names.

Levels are ``posix_session`` (own session and process group, killed as a
group), ``windows_job`` (job object with kill on close) and
``windows_tree_best_effort`` (taskkill of the process tree, which misses
re-parented descendants). Only the first two count as ``strong``.

#### `ContainmentCapabilities.strong`

```python
strong() -> bool
```

True when the level can reliably terminate the whole tree.

### `ProcessIdentity`

A process id plus a start token that stays unique across pid reuse.

### `process_identity`

```python
process_identity(pid: int) -> ProcessIdentity | None
```

Return the live identity of ``pid``, or None if it is gone or only a zombie.

### `probe_capabilities`

```python
probe_capabilities() -> ContainmentCapabilities
```

Report this host's containment level and which resource limits it enforces.

### `ContainedProcess`

One launched worker process and the means to kill its whole tree.

Create it with :meth:`spawn`. The worker receives its assignment on stdin
(:meth:`feed`) and returns one receipt line on stdout (:meth:`start_reader`,
:meth:`output`). stderr is discarded. The leader's exit status is observed
without reaping it on POSIX, so the group is killed before the pid can be
recycled.

#### `ContainedProcess.spawn`

```python
spawn(cls, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | None = None, limits: ResourceLimits | None = None, containment_id: str | None = None, require_strong: bool = True) -> 'ContainedProcess'
```

Start ``argv`` through the launcher in its own session or job object.

Raises :class:`ContainmentFailure` when ``require_strong`` is set and the
host can only offer best-effort containment. Raises ``OSError`` when the
process cannot be started at all.

#### `ContainedProcess.returncode`

```python
returncode() -> int | None
```

Exit status once the leader has exited and been reaped, else None.

#### `ContainedProcess.confirmed`

```python
confirmed() -> bool | None
```

True when the whole tree was proven dead, False when not, None while running.

#### `ContainedProcess.overflowed`

```python
overflowed() -> bool
```

True once the worker wrote more than the allowed output bytes.

#### `ContainedProcess.feed`

```python
feed(payload: bytes) -> None
```

Write ``payload`` to the worker's stdin in a thread, then close stdin.

#### `ContainedProcess.start_reader`

```python
start_reader(max_output_bytes: int) -> None
```

Collect stdout in a thread, stopping and flagging overflow past the limit.

#### `ContainedProcess.output`

```python
output() -> bytes
```

Return collected stdout after the tree is dead (waits briefly for the reader).

#### `ContainedProcess.poll`

```python
poll() -> int | None
```

Return the exit status once the leader exited, else None.

On first observing the exit, every remaining descendant is killed and
the leader is reaped, so a worker can never leave a tree behind by
exiting. Check :attr:`confirmed` for whether that was proven.

#### `ContainedProcess.terminate_tree`

```python
terminate_tree(grace_seconds: float = 3.0) -> bool
```

Terminate the leader and all descendants. True only if proven dead.

POSIX sends SIGTERM to the group, waits up to ``grace_seconds`` for the
group to empty, then SIGKILL. Windows kills the job immediately (there is
no catchable signal), or falls back to ``taskkill /T /F``.

#### `ContainedProcess.close`

```python
close() -> None
```

Release pipes, helper threads and the job handle. Safe to call repeatedly.

### `ProcessRegistry`

Durable table of contained worker processes, in the same SQLite database.

The table ``contained_processes`` is created lazily and additively, in the
same way as ``local_coordinator_lease``. No existing table changes and the
schema version stays 2. It stores process identity and claim references
only: never argv, environment, task text, tokens or output.

Write order is: intent row (``spawning``), spawn, then ``running`` with the
pid and start token. Pass ``create=False`` for a read-and-reap view that must
not create the table on installations that never used containment.

#### `ProcessRegistry.bind_leader_generation`

```python
bind_leader_generation(generation: int) -> None
```

Stamp later rows with the coordinator's leadership generation (0 means none).

#### `ProcessRegistry.record_intent`

```python
record_intent(*, workflow_id: str, task_id: str, attempt: int, generation: int, level: str, argv_sha256: str, owner: ProcessIdentity | None = None, leader_generation: int | None = None) -> str
```

Insert a ``spawning`` row before the process exists and return its id.

#### `ProcessRegistry.record_started`

```python
record_started(containment_id: str, *, pid: int, pgid: int, identity: ProcessIdentity | None) -> None
```

Attach the pid, group id and start token and mark the row ``running``.

#### `ProcessRegistry.mark`

```python
mark(containment_id: str, state: str) -> None
```

Move a row to a new state. Terminal states also stamp ``ended_at``.

#### `ProcessRegistry.running`

```python
running() -> list[dict]
```

Rows still believed alive (``spawning`` or ``running``), oldest first.

### `OrphanReaper`

Clean up worker trees left behind by a dead or replaced coordinator.

A row is an orphan when its owner process is dead, or when it was started
under an older leadership generation (the owner then lost its fence). A tree
is killed only when the live process at the recorded pid has the recorded
start token, so a recycled pid is never signalled. If the leader is gone but
its group is still alive, ownership cannot be verified: the row is marked
``unconfirmed``, nothing is killed, and the task is blocked. The journal
remains the only authority over task state.

#### `OrphanReaper.reap`

```python
reap(*, leader_generation: int) -> dict
```

Examine every live row and return counts per outcome.

The result keys are ``examined``, ``terminated``, ``gone``,
``identity_mismatch`` and ``unconfirmed``. Counts only, no identifiers.

### `ContainedWorker`

Run each task in a killable child process with a hard deadline.

``argv`` is the fixed command line, an operator decision made in code. The
child gets the :class:`TaskAssignment` as one JSON object on stdin, with no
owner token and no generation, and answers with one JSON receipt line on
stdout. stderr is discarded. Its environment is an allowlist: ``PATH``
(plus ``SYSTEMROOT``, ``TEMP`` and ``TMP`` on Windows) and the names in
``env_passthrough``. The child's working directory is the project path.

The deadline is monotonic and starts at spawn, independent of the lease.
Heartbeats continue until the deadline. The host discards any receipt that
arrives after cancellation began. ``require_strong`` is always treated as
true for ``code`` tasks. This class never grants write, model or network
permission: the argv you choose decides what the child can do.

Override guidance: subclass only to change how the argv is chosen. Do not
bypass the registry, because crash recovery depends on its rows.

#### `ContainedWorker.execute`

```python
execute(lease: TaskAssignment) -> WorkerReceipt
```

Run the task with a private, never-cancelled control (used outside a supervisor).

#### `ContainedWorker.execute_controlled`

```python
execute_controlled(assignment: TaskAssignment, control: ExecutionControl) -> WorkerReceipt
```

Spawn, supervise and tear down the child. Returns its receipt or raises.

Raises :class:`WorkerDeadlineExceeded`, :class:`WorkerCancelled`,
:class:`WorkerCrashed`, :class:`WorkerOutputInvalid` or
:class:`ContainmentFailure`. Whenever it returns or raises after a spawn,
the child's whole tree has been killed, or the failure says it could not
be proven.

## `decisionbound.background`

Crash-tolerant, single-device background coordination for local DAG workflows.

A running process is deliberately NOT a durable authority: SQLite owns the
leadership lease, workflow claims and receipts. An external operating-system
service manager should restart this process after power loss. This module never
starts model calls, shells or coding workers implicitly; injected workers must
be independently authorized and sandboxed by their operators.

This is **not** a distributed consensus implementation, a task-killing sandbox,
or a multi-tenant cloud service. It is a bounded local service primitive.
Workers that run as ``ContainedWorker`` child processes are killed on deadline,
cancel, stop or lost leadership, and a restarted coordinator reaps the trees of
a crashed one. In-process thread workers still cannot be forcibly stopped.

### `CoordinatorConflict`

Coordinator ownership is stale, busy, or cannot be safely renewed.

### `ResourceBudgetExceeded`

The local process exceeded an operator-defined resource admission cap.

### `resident_memory_mib`

```python
resident_memory_mib() -> float
```

Measure current process RSS without third-party dependencies.

On Linux, use the process' resident page count. On Windows, use the current
working set from ``GetProcessMemoryInfo``. On macOS or other POSIX
platforms, fall back to ru_maxrss (peak rather than current RSS). Unsupported
platforms fail closed when a resource cap is requested, never claim 0 MiB.

### `CoordinatorSettings`

Small bounded service configuration with explicit local resource caps.

``max_seconds`` limits the coordinator's scheduling lifetime, not the
execution time of arbitrary worker processes. Worker plugins MUST enforce
their own hard process/network deadlines, or run inside a ``ContainedWorker``.
``max_rss_mib=None`` disables the RSS guard explicitly; otherwise an
unavailable measurement fails closed. ``worker_stop_grace_seconds`` is how
long a graceful stop lets running workers finish before they are cancelled.

### `Leadership`

Private fencing credentials; never transmit to untrusted workers.

### `LocalCoordinatorLeader`

Exclusive SQLite-backed leadership lease for one local background loop.

All ownership transitions use ``BEGIN IMMEDIATE`` and generation/token CAS.
Expiry permits leadership takeover only after the old lease dies; a paused
old process cannot renew or release the new owner's lease. Actual worker
task ownership is separately fenced by :class:`WorkflowJournal`.

#### `LocalCoordinatorLeader.acquire`

```python
acquire(*, ttl_seconds: int = 30, now: float | None = None) -> Leadership
```

Obtain unique leadership or fail without replacing an active owner.

#### `LocalCoordinatorLeader.renew`

```python
renew(leadership: Leadership, *, ttl_seconds: int = 30, now: float | None = None) -> None
```

Extend only the live generation; reject paused or replaced owners.

#### `LocalCoordinatorLeader.release`

```python
release(leadership: Leadership) -> bool
```

Clear only this owner's lease; never clear a replacement's lease.

#### `LocalCoordinatorLeader.status`

```python
status(*, now: float | None = None) -> dict
```

Expose read-only coordinator health without revealing credentials.

### `BackgroundCoordinator`

Restartable local service that schedules bounded, explicitly registered workers.

``workers`` is an allowlisted registry; default policy enables only offline
preparation. Operator-approved coding workers may be injected by the SDK,
but this daemon never grants them write/model/network authority itself.
The service runs until asked to stop, killed, or its leadership is lost.
On restart, SQLite leases and journals determine what can safely resume.

Override guidance: inject custom :class:`WorkflowWorker` implementations
and :class:`IndependentTaskVerifier` implementations, not a replacement
journal that bypasses mandatory host evidence checks.

#### `BackgroundCoordinator.run`

```python
run(*, stop_event: threading.Event | None = None, max_seconds: float | None = None, once: bool = False) -> dict
```

Poll the durable queue while renewing leadership in a separate thread.

``once=True`` runs one bounded pass. ``max_seconds`` is useful for local
certification and graceful maintenance. For unattended days-long use,
install this explicit CLI process as a restart-on-failure OS service.
Internal heartbeats never grant a task verification status.

## `decisionbound.review_gate`

Local human review of code artifacts, with tamper-evident export authorization.

An approval authorizes **exporting a patch file only**. It cannot mark a task
completed, approve execution, merge, commit, push, or change a source checkout.
The service uses the Sprint 04 artifact ledger and Sprint 07 workflow journal,
so there is no second source of truth for test results or code provenance.

The review decision is a local OS-user action, NOT a claim of authenticated
human identity or remote enterprise authorization. Subclasses may only add
vetoes; the host's canonical integrity checks remain mandatory.

### `ReviewUnavailable`

The workflow has no safe, unambiguous coding artifact to review.

### `ReviewGitUnavailable`

Git could not confirm the source commit or patch scope, so review is refused.

Raised when Git is missing, times out, exits with an error or the
repository is unreadable. Approval and export fail closed on it. The
message is fixed and never carries paths, Git stderr or environment text.

### `ReviewConflict`

The review is stale, missing consent, or conflicts with another client.

### `ReviewSnapshot`

A read-time view of the exact immutable artifact and source state.

``fingerprint`` includes workflow, project, commit, contract, run, patch,
and current verification status. This is a short-lived authorization input,
not a promise that the filesystem will remain unchanged afterward.

#### `ReviewSnapshot.to_dict`

```python
to_dict() -> dict[str, Any]
```

Return metadata without code text, secrets, or local filesystem paths.

### `ExportReviewPolicy`

Optional veto-only policy for organizations extending export review.

Override ``allow`` to add more restrictions. It cannot grant export rights,
weaken stale-evidence checks or bypass the required local approval ledger.

#### `ExportReviewPolicy.allow`

```python
allow(snapshot: ReviewSnapshot) -> bool
```

Return false to veto an otherwise valid export.

### `LocalReviewPolicy`

Accept exports only after all mandatory host-side gates have passed.

#### `LocalReviewPolicy.allow`

```python
allow(snapshot: ReviewSnapshot) -> bool
```

Do not add an additional veto on top of host verification.

### `ReviewGate`

Snapshot, approve/reject, revoke, and export one coding patch.

Args:
    repository: The SQLite store owning the workflow and repair journals.
    state_root: Private state directory, separate from the Git checkout.
    policy: Additional veto-only policy; never used to grant permission.

The caller must already have local OS-user authorization. Local HTTP
access additionally requires the pairing token. MCP only exposes review
metadata; it cannot approve or export patches.

#### `ReviewGate.snapshot`

```python
snapshot(workflow_id: str, *, include_diff: bool = False) -> dict[str, Any]
```

Inspect a pending patch, optionally returning its local-only raw diff.

The diff contains source code and must never be persisted to event
logs or exposed as an MCP tool response. HTTP diff access requires an
authenticated local-user token and an explicit ``include_diff`` query.

#### `ReviewGate.decision`

```python
decision(workflow_id: str) -> dict[str, Any]
```

Return the latest local approval revision; never return code text.

#### `ReviewGate.decide`

```python
decide(workflow_id: str, *, expected_fingerprint: str, expected_revision: int, action: str, acknowledge_unverified: bool = False) -> dict[str, Any]
```

Record a local approve/reject/revoke action with optimistic locking.

Approval is not a claim of human identity or task correctness. A
rejected fingerprint cannot be re-approved; new evidence is required.
Revocation needs the expected current revision but works even when a
patch has subsequently become stale.

#### `ReviewGate.history`

```python
history(workflow_id: str) -> list[dict[str, Any]]
```

Read metadata-only local review decisions and export event history.

#### `ReviewGate.export`

```python
export(workflow_id: str, *, destination: Path, expected_fingerprint: str, expected_revision: int) -> dict[str, Any]
```

Export only an explicitly approved, still-current code patch.

This method does not apply or merge code. It refuses destinations inside
the source checkout and delegates exclusive-file creation and checksum
validation to Sprint 04's patch-export component.

## `decisionbound.client_sync`

Read-only, cursor-based local client synchronization for DecisionBound.

The CLI, browser, and editor read *one* SQLite-backed workflow ledger.  This
module does not create a second session store or trust client-supplied results.
All queries are project-scoped, bounded, parameterized and metadata-only.

A session currently represents a durable workflow (``wf_*``). Future protocol
versions may include other run types, without pretending that legacy runs are
synchronized automatically. Event cursors are SQLite's global, monotonically
increasing workflow event IDs; clients can reconnect using ``after``.

### `LocalSyncError`

Invalid project, session, cursor, or protocol parameter.

### `LocalClientSync`

Provide bounded, project-scoped views of the canonical workflow journal.

Args:
    repository: The same SQLiteRepository used by all other clients.

Override contract:
    Read methods may be replaced for another storage backend, but must
    enforce both bearer-token checks at the transport layer *and* project
    scoping in the data layer. Never accept a caller's project ID alone as
    evidence of identity; local use is gated by an explicit pairing token.

Security invariants:
    This class never returns model prompts, code diffs, local file paths,
    authentication tokens, lease credentials, or worker-provided completion
    assertions. It does not modify the workflow ledger.

#### `LocalClientSync.capabilities`

```python
capabilities() -> dict[str, Any]
```

Advertise the implemented contract, not future experimental APIs.

#### `LocalClientSync.list_sessions`

```python
list_sessions(project_id: str, *, limit: int = 25, before: int | None = None) -> dict[str, Any]
```

Page workflow sessions by immutable creation event sequence.

``before`` is an exclusive creation-event ID; it remains stable when
other clients append events to existing workflows. Session IDs and
workflow states are canonical, not client caches.

#### `LocalClientSync.snapshot`

```python
snapshot(project_id: str, session_id: str) -> dict[str, Any]
```

Return the verified journal's safe workflow status for one project.

#### `LocalClientSync.review_state`

```python
review_state(project_id: str, session_id: str) -> dict[str, Any]
```

Read review approval and current integrity without exposing patch text.

An unavailable/stale patch is represented as unavailable metadata with
its *historical* decision still visible. Never suggest that an old
approval is presently exportable without a fresh evidence snapshot.

#### `LocalClientSync.session_events`

```python
session_events(project_id: str, session_id: str, *, after: int = 0, limit: int = 50) -> dict[str, Any]
```

Read one session's events after an exclusive durable event cursor.

#### `LocalClientSync.changes`

```python
changes(project_id: str, *, after: int = 0, limit: int = 50) -> dict[str, Any]
```

Read one project's global change feed for safe reconnect/resume.

The cursor is global across *all* projects. Skipped IDs are normal:
they may belong to other projects. Clients must use ``next_after``
rather than assuming consecutive event IDs.

## `decisionbound.local_auth`

Local pairing token lifecycle, independent of optional FastAPI dependencies.

Both `dbound client pair` and the optional web server use this module. The
credential is created with restrictive permissions and is never embedded in a
repository, export, API response, or extension workspace setting.

### `local_token`

```python
local_token() -> str
```

Create or read one user-scoped loopback pairing token.

Refuses a symlinked credential and avoids overwriting an existing token.
The caller is responsible for checking `Authorization: Bearer` and
explicitly requesting token disclosure from the CLI if needed.

## `decisionbound.onboarding`

Safe end-user onboarding and deterministic runtime readiness diagnostics.

Setup is intentionally separate from execution: selecting a profile cannot
start a model, obtain a credential, turn on networking, or authorize a write.
Diagnostics disclose only capability presence, never credential values or
private paths. The module has no web/API dependency, so CLI and web clients
share the same decision logic and unit-test it without third-party services.

### `SetupCheck`

A credential-free, deterministic prerequisite check.

``ok`` is about local readiness, not successful provider authentication or
certification of sandbox guarantees. ``required`` distinguishes blockers
from optional tooling. Never add environment variable *values* to details.

### `SetupReport`

End-user setup snapshot suitable for CLI JSON or authenticated HTTP.

``ready`` means the selected offline path can run without new prerequisites.
For external configurations, it only means prerequisites are present; a
real authenticated connection remains untested until the user opts in.

#### `SetupReport.to_dict`

```python
to_dict() -> dict
```

Return a serializable report without secrets or private filenames.

### `SetupService`

Manage an end user's first-run selection and inspect prerequisites.

This service can be replaced by a client UI, but should remain the final
policy boundary for profile selection and project registration. A configured
profile never grants model, network, tool or worktree write consent.

Args:
    store: Shared SQLite registry for project identity and active profile.
    environ: Injectable environment for tests; only key presence is read.
    which: Injectable executable resolver; no executable is invoked.

#### `SetupService.inspect`

```python
inspect(profile: str | None = None, project_path: Path | None = None) -> SetupReport
```

Inspect one profile without creating files or using network services.

Missing project identity is reported rather than repaired implicitly.
This allows ``dbound doctor`` and a setup screen to be side-effect-free.

#### `SetupService.configure`

```python
configure(profile: str, project_path: Path | None = None, initialize_project: bool = False) -> SetupReport
```

Select a profile and optionally register a requested repository.

Invalid profile names fail before any repository initialization. This
intentionally does not accept secrets, expose keys, or test models.
A project is never initialized unless ``initialize_project`` is true.

## `decisionbound.state_integrity`

Auditable, offline SQLite health checks and consistent backup/recovery.

This module deliberately operates on *one local user's* DecisionBound database.
It does not provide distributed transactions, disaster-recovery automation, or
transparent schema migration.  Backups contain private project/task metadata;
callers must handle their paths as sensitive user data.

Extension contract: tools may wrap ``inspect_database`` and
``create_backup`` for user interfaces.  They must not bypass the no-overwrite,
read-only source, atomic publication, or schema/integrity checks.  Recovery
always creates a separate file and never replaces an active runtime database.

### `StateIntegrityError`

Source snapshot failed integrity validation, or cannot be read safely.

### `StateDestinationError`

Unsafe backup destination, overwrite request or incompatible recovery target.

### `StateHealth`

Credential-free report; a healthy database is not a production certification.

The report records schema and integrity validation, but contains no raw
project names, decisions, API credentials, prompt text, or stored events.

#### `StateHealth.to_dict`

```python
to_dict() -> dict[str, Any]
```

Expose stable JSON-compatible read-only diagnostics.

### `StateSnapshot`

Successful consistent snapshot, including its independently checked hash.

#### `StateSnapshot.to_dict`

```python
to_dict() -> dict[str, Any]
```

Return metadata only; snapshot bytes are never emitted to stdout.

### `inspect_database`

```python
inspect_database(path: str | Path, *, full: bool = False) -> StateHealth
```

Validate an existing database without creating it or changing user state.

Args:
    path: Existing SQLite database to inspect.
    full: Run PRAGMA integrity_check rather than the faster quick_check.
        Full checks are useful before release or recovery but can take
        longer for large user databases.

Returns:
    A non-secret report for both passing and failing integrity checks.

Raises:
    StateIntegrityError: Invalid source path or unopenable/corrupt SQLite.

### `create_backup`

```python
create_backup(source: str | Path, destination: str | Path) -> StateSnapshot
```

Create an immutable point-in-time backup without stopping the local API.

No network services are used. The output is sensitive and should never
be checked into Git. Nothing replaces the live database. Any existing
destination is refused, even if it is empty.

### `restore_to_new_path`

```python
restore_to_new_path(backup: str | Path, destination: str | Path, *, active_path: str | Path | None = None) -> StateSnapshot
```

Validate a backup and recover into a *new*, never-overwritten DB file.

The caller may then inspect the recovered file and migrate it through a
separately approved procedure. This operation is deliberately not an
in-place recovery command: replacing an active WAL database can destroy
in-flight work or cause split-brain state across running processes.

## `decisionbound.state_migration`

Explicit, non-destructive schema upgrades for a single-user local SQLite state.

This adapter does not provide rolling distributed migrations. It produces a
verified *new* database file while preserving the original, including its WAL.
The user must stop old runtimes and explicitly activate the new path after
reviewing the migrated copy. No migration is performed on ordinary startup.

### `StateMigrationError`

An unsupported, unsafe, or unsuccessfully verified upgrade attempt.

### `MigrationReport`

Public, metadata-only receipt for one non-overwriting state upgrade.

#### `MigrationReport.to_dict`

```python
to_dict() -> dict
```

Serialize migration metadata without storing or returning source contents.

### `migrate_database`

```python
migrate_database(source: str | Path, destination: str | Path) -> MigrationReport
```

Prepare an upgraded, integrity-checked SQLite copy and preserve the source.

Supports legacy v1 -> current and current -> current (validated export).
Future, unversioned, and corrupt databases are rejected. A failed upgrade
never publishes an output. Publication is atomic and refuses existing paths.

This function does NOT activate the new database, change the active state
location, or replace the source. A caller must stop all old processes before
manually switching its state directory or restoring the migrated copy.

## `decisionbound.transferbench`

Deterministic, non-LLM transfer-decision benchmarking and safe baselines.

This is a research *instrument*, not evidence of empirical improvement.
Oracle labels are author-written data, not produced by running the candidate
policy being evaluated, and they are not independently reviewed. Models, model prices, tool costs and verified
software outcomes are deliberately not inferred from these fixture labels.

### `TransferCase`

One oracle-labeled transfer opportunity and its target context.

``repo_family`` is the target repository family used for cluster bootstrap.
``allowed_actions`` holds author-written labels, not produced by running the
tested policy.
``policy_conflict`` means an unjustified action could violate owner policy;
it is NOT a claim that a real code change or real regression occurred.

### `load_cases`

```python
load_cases(path: str | Path) -> tuple[TransferCase, ...]
```

Load bounded JSONL labels and validate held-out repository disjointness.

The format is versioned as ``transferbench-case-v1``. Unknown fields or
invalid actions fail closed; no arbitrary Python or YAML objects are read.

### `predict`

```python
predict(method: str, case: TransferCase) -> str
```

Return one policy action without using the oracle label or label rationale.

### `evaluate`

```python
evaluate(cases: tuple[TransferCase, ...], *, split: str = 'test', seed: int = 17, bootstrap_samples: int = 1000) -> dict[str, Any]
```

Evaluate fixed baseline policies; report only policy-oracle agreement.

No model is called. No software patch is applied. Results cannot support
claims of real verified task success, deployment reliability, or cost savings.

## `decisionbound.release_trust`

Offline Ed25519 release attestation; does not download or install software.

A fleet client must pin the publisher's independently distributed public key.
A hash recorded inside an unsigned ZIP proves only integrity, not authenticity.
This module verifies an explicit detached signature over canonical manifest
bytes, the expiry window, downgrade policy, and each named artifact's SHA-256.
It does not implement automated rollout, key rotation or secure clock services.

### `ReleaseTrustError`

The release signature, policy, metadata or artifact is untrusted.

### `canonical_manifest`

```python
canonical_manifest(manifest: dict[str, Any]) -> bytes
```

Canonical UTF-8 serialization to sign or verify (plus final newline).

### `verify_signed_release`

```python
verify_signed_release(manifest_path: str | Path, signature_path: str | Path, public_key_pem: str | Path, artifacts_directory: str | Path, *, installed_version: str, minimum_accepted_version: str | None = None, now: datetime | None = None) -> dict[str, Any]
```

Verify a publisher signature, downgrade/expiry policy, and local artifacts.

Public keys must arrive through a trusted, out-of-band administrator or
enterprise policy; fetching a key from the same untrusted release URL is
NOT authentication. This function never executes any release artifact.

Args:
    manifest_path: Canonical JSON UTF-8 metadata, maximum 64 KB.
    signature_path: Base64-encoded detached Ed25519 signature.
    public_key_pem: Trusted pinned Ed25519 PEM public key.
    artifacts_directory: Directory containing already-downloaded artifacts.
    installed_version: Currently installed client version; downgrade floor.
    minimum_accepted_version: Optional persistent fleet anti-rollback floor.
    now: UTC clock for explicit tests; production uses UTC wall clock.
