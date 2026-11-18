# Security, privacy and limitations

## Default guarantees of this alpha

- No API key is required to run local commands.
- No automatic network calls, paid model requests, remote telemetry or code writes.
- Local web API binds loopback only and requires a per-user bearer token for state access.
- Imported profiles reject unknown keys and obvious credential-key fields; **never rely on this as a comprehensive secrets scanner**.
- Source evidence paths must stay inside the project root, including symlink resolution.
- Database files reject symlink targets and use local POSIX restrictive permissions when possible.
- Approval requires evidence hash equality; unknown changes require rediscovery and review.
- Tool calls require allowlisted names and an explicit write grant if they have side effects.

## Threat model

| Threat | Current control | Remaining limitation |
| --- | --- | --- |
| Prompt injection from ADR or skill | Treated as data, never automatic policy | A future model tool loop needs stronger sandboxing and adversarial tests |
| Prompt injection through tool output (text inside a repository file) | The model planner wraps each tool result between `<<<UNTRUSTED_TOOL_OUTPUT>>>` and `<<<END_UNTRUSTED_TOOL_OUTPUT>>>`, breaks any run of three or more `<` or `>` inside the content so it cannot fake a marker, names the tool outside the markers, and tells the model the text is data. A cut result carries a note after the closing marker | This is marking, not a defence. It does not show that any model resists injection, and no adversarial run with a real model has been done. Tool permissions are the real control |
| Planner stuck repeating one tool call | After `max_identical_calls` identical calls in a row (default 3) the next one raises `RepeatedCallError` before the tool runs | Only identical consecutive calls are caught. A planner that alternates two calls is stopped only by the step budget |
| Run that takes too long | Optional `deadline_seconds` stops the loop before the next planner or tool call and raises `AgentDeadlineExceeded` | Off by default. It cannot interrupt a planner or tool call that is already running, and a `Finish` returned after the deadline is still accepted |
| Leaked provider credentials | Built-in profiles carry no keys | Environment/keychain integration pending |
| Unapproved cross-project memory | Scope, owner and explicit tag checks | Does not yet support enterprise identity or policy reconciliation |
| Unauthorized localhost browser requests | Token, Host and Origin checks | No remotely deployed OAuth/SSO; local process compromise out of scope |
| Hung or runaway contained worker | Hard deadline, process tree kill, resource limits, bounded output, orphan reaping on restart | Not a sandbox: `setsid` and job breakaway escape, no filesystem or network isolation. Tested in CI on Linux, macOS and Windows. See [Worker containment](worker-containment.md) |
| Duplicate write after crash | Only read-only worker operations shipped | Durable write reconciliation remains future work |
| Unsound model verification | Current verifier only checks preparation | Independent code/test artifact verifier pending |

## Agent loop stop reasons

The read-only loop emits a `loop_stopped` event with `reason` (`repeated_call`, `deadline` or `step_budget`), `steps_run`, and for a repeated call the tool `name`. It never carries arguments or tool output. All three stops raise a `RuntimeError` subclass or `RuntimeError` itself, so existing handlers keep working. Stored observations keep the shape `{"tool": ..., "result": ...}` that the evidence verifier parses and whose size and hash the ledger verifier checks, and the tool name comes first so a cut observation still shows it. The markers are added only when the planner builds its request, so stored sizes and hashes are unchanged. They add about 75 estimated input tokens per observation shown (about 300 for the four the planner sends), which counts against the planner's input bound.

## Fixed limits and the test behind each one

These limits hold whatever the profile, a model or a planner asks for. A caller who writes code against the library can still pass `allow_writes=True` to the registry or `approved_writes=True` to a workflow runner. The limits below are the checks that stand between a model and those grants, and the command line flags that stand between a user and them. Each row names the code and a test that fails if that check is removed. Each test except the Docker one was checked by removing the code check and confirming the test fails. The Docker probes were checked the same way when they were added, and need a real daemon. Test paths are under `tests/`.

| Limit | Code | Test that fails without it |
| --- | --- | --- |
| The read-only agent loop never passes write permission, even if the planner asks for a side-effecting tool | `BoundedReadOnlyAgent.run` in `agent_loop.py` | `unit/test_core.py::test_readonly_loop_refuses_model_requested_write` |
| A tool runs only if its name is on the allowlist for the run | `ToolRegistry.call` in `tools.py` | `unit/test_core.py::test_registered_read_tool_is_refused_when_the_run_allowlist_excludes_it` |
| A side-effecting tool needs an explicit write grant from the host | `ToolRegistry.call` in `tools.py` | `unit/test_core.py::test_tool_registry_never_grants_unlisted_call` |
| Read-only file tools refuse a path that goes through a symlinked directory | `repository_readonly_tools` in `agent_loop.py` | `security/test_security_boundaries.py::test_readonly_repository_tools_reject_symlinked_parent_directory` |
| Selecting a profile never reports write consent as given | `SetupService.inspect` in `onboarding.py` | `unit/test_onboarding_sprint10.py::test_writes_are_never_approved_by_profile_selection` |
| Selecting a profile never reports network access as granted, even a custom profile with `network: true` | `SetupService.inspect` in `onboarding.py` | `unit/test_onboarding_sprint10.py::test_network_access_is_never_reported_as_granted_by_profile_selection` and `::test_a_profile_that_sets_network_true_still_reports_no_network_consent` |
| `dbound agent run` makes no model call without `--allow-remote` | `agent_run` command in `cli.py` | `integration/test_agent_cli_web.py::test_agent_cli_remote_denied_without_user_opt_in` |
| A provider transport refuses to call out unless it was created with `enabled=True` and finds its key in the environment | `OpenAICompatibleJSONTransport.complete` in `providers.py` (the Anthropic transport has the same check) | `unit/test_provider_adapters.py::test_remote_requires_explicit_opt_in_and_key` |
| `dbound code apply` refuses unless both `--approve-writes` and `--allow-docker` are given | `code_apply` command in `cli.py` | `integration/test_code_cli.py::test_cli_requires_explicit_two_factor_write_consent` (neither flag) and `::test_code_apply_needs_both_flags_not_just_one` (each flag alone) |
| `code mock-run` and `code repair-mock` refuse without `--approve-writes` | `code_mock_run` and `code_repair_mock` in `cli.py` | `integration/test_code_cli.py::test_code_mock_run_and_repair_mock_refuse_without_approve_writes` |
| `code propose`, `code repair-provider` and `code native-propose` refuse unless every one of their three consent flags is given | the three commands in `cli.py` | `integration/test_code_cli.py::test_model_and_native_write_commands_need_every_consent_flag`, which drops each flag in turn |
| A supervised coding workflow is not created without explicit write approval | `SupervisedCodingOrchestrator.execute` in `supervised_coding.py` | `integration/test_supervised_sprint07.py::test_requires_explicit_writes_consent_before_creating_workflow` |
| A read-only run never reports `task_completed: true` | `run_readonly_agent` in `execution.py` | `integration/test_agent_execution.py::test_two_tool_steps_evidence_verification_and_trace_redaction` |
| A review approval is recorded only against the exact fingerprint of the patch, commit and evidence | `ReviewGate.decide` in `review_gate.py` | `integration/test_review_gate_sprint08.py::test_bad_fingerprint_and_id_fail_before_approval` |
| A `ReviewGate` export is refused if the patch or checkout changed after approval | `ReviewGate.export` and `ReviewGate.snapshot` in `review_gate.py` | `integration/test_review_gate_sprint08.py::test_patch_tampering_and_git_commit_change_invalidate_authorization` and `::test_export_rechecks_the_fingerprint_it_was_approved_with` |
| A model-run test container has no network but loopback, a read-only root, no capabilities and a pids limit | `DockerTestRunner` in `code_sandbox.py` | `integration/test_docker_isolation_real.py`. These need a real Docker daemon and run in CI only, so they are skipped on a machine without one |

What these rows do and do not mean:

- Profile selection only reports consent. The rows for profiles show that a profile never sets consent. The limit that stops a write is the `--approve-writes` and workflow approval rows.
- `task_completed` is set false in many places. Only the read-only run is named here, and only the assignment in `run_readonly_agent` is guarded by that test. The review, repair and workflow paths each have their own assertions, for example in `integration/test_review_gate_sprint08.py`.
- The export rows apply to `ReviewGate`, the review console and its commands. The older `dbound code export` command writes a patch that passed its SHA-256 check and needs `--acknowledge-unverified` for an unverified one. It has no approval step and is not covered by these rows.
- The model and native commands check their consent flags before they read the contract or contact anything. The tests show each missing flag stops the command with the consent message. They do not show what the command does when all flags are given, which needs Docker or a provider and is covered elsewhere.
- The export check runs twice by design. A changed patch or a moved checkout is usually refused earlier, when the snapshot is rebuilt. The final fingerprint comparison covers the case where the evidence still loads but hashes differently, and its test substitutes the snapshot to reach it.
- The patch hash is also compared with the changed-file receipt in `ReviewGate.snapshot`. That comparison repeats a check made earlier in `_patch_bytes`, so no test fails when only the second one is removed. It is defence in depth and is not counted as a limit.

## Decision: a denied tool call ends the run

When a planner proposes a tool that is not allowed, or a side-effecting tool without a write grant, `ToolRegistry.call` raises `ToolPermissionDenied` and `BoundedReadOnlyAgent.run` does not catch it. The loop ends on the first denial. The planner is not asked again and no tool runs. The proposal is logged as a `tool_invocation` event (name, argument keys and a hash), and no observation is recorded.

Two other paths end a run in a similar way. The built-in model planner checks each proposed tool against the offered allowlist when it parses the reply, and raises `ModelOutputError` for a tool it did not offer, so a model cannot reach `ToolPermissionDenied` for an unlisted name. `ToolPermissionDenied` comes from a custom planner, a custom registry, or an allowlisted side-effecting tool that is called without a write grant.

This applies to the loop, not to a whole workflow. A workflow task that raised an error can be retried when it is a `prepare`, `inspect` or `review` task with zero cost and attempts left (`WorkflowJournal.fail`, `max_attempts` of 1 to 4). That retry starts a new run of the loop with the same limits, so a denial inside it ends that run too. A task that writes code is blocked after a failure and does not retry.

The reason is that a read-only run is unattended, so there is nobody to ask. Retrying inside the loop would give a model repeated chances to probe the permission boundary and would spend budget. Some interactive harnesses count denials and ask the user after a number of them, which suits a session with a person present and does not suit this one. `tests/unit/test_core.py::test_first_denied_tool_call_ends_the_run_and_the_planner_is_not_asked_again` fails if the loop starts continuing after a denial. If an interactive mode is added later, it should be a separate runner and not a change to this loop.

## Security boundaries for contributors

Do not use model prompts as authorization. Do not elevate candidate memories to approved policies automatically. Do not use a public GitHub Pages site to receive or store developer secrets. Never publish private repository content, unreleased code or protected benchmark fixtures as documentation.

For a vulnerability report, follow the project's `SECURITY.md` contact policy; do not post active secrets in a public issue.

## Coding-agent write path

See [Sandboxing & threat model](sandboxing.md) and [Coding agents](coding-agents.md). Model-proposed diffs are untrusted, write consent is required, and the host enforces strict Git patch/path policies. Real untrusted tests require opt-in, network-disabled Docker isolation. The repository is not a certified multi-tenant code-execution service.
