"""Human-oriented CLI for the safe local DecisionBound alpha.

The CLI uses the same services and SQLite registry as the optional web API.
Every command can operate without an LLM account or third-party credentials.
"""
from __future__ import annotations
import json
from pathlib import Path
from dataclasses import asdict
import typer
from .identity import init_project, load_project
from .profiles import ProfileManager
from .services import DecisionService
from .settings import repository
from .runtime import Supervisor
from .skills import SkillRegistry

app = typer.Typer(help="DecisionBound — evidence-grounded engineering decisions", no_args_is_help=True)
profiles_cli = typer.Typer(help="Manage end-user profiles", no_args_is_help=True)
decisions_cli = typer.Typer(help="Review source-linked engineering decisions", no_args_is_help=True)
app.add_typer(profiles_cli, name="profile")
app.add_typer(decisions_cli, name="decision")
mcp_cli = typer.Typer(help="Read-only MCP v2 integration for one initialized project")
app.add_typer(mcp_cli, name="mcp")
agent_cli = typer.Typer(help="Run strictly bounded, read-only model/tool agents")
app.add_typer(agent_cli, name="agent")
code_cli = typer.Typer(help="Consent-gated code patches in disposable Git worktrees", no_args_is_help=True)
app.add_typer(code_cli, name="code")
workflow_cli = typer.Typer(help="Durable bounded DAG workflows and local supervisor", no_args_is_help=True)
app.add_typer(workflow_cli, name="workflow")
review_cli = typer.Typer(help="Human review and explicitly approved patch exports", no_args_is_help=True)
app.add_typer(review_cli, name="review")
client_cli = typer.Typer(help="Pair and inspect canonical local sessions across CLI, web and editor", no_args_is_help=True)
app.add_typer(client_cli, name="client")
state_cli = typer.Typer(help="Offline SQLite integrity checks and non-overwriting recovery", no_args_is_help=True)
app.add_typer(state_cli, name="state")
release_cli = typer.Typer(help="Verify signed, already-downloaded software releases", no_args_is_help=True)
app.add_typer(release_cli, name="release")


def _print(value: object) -> None:
    """Write stable machine-readable JSON for scripts and other clients."""
    typer.echo(json.dumps(value, sort_keys=True, indent=2, default=str))


@app.command("setup")
def setup_user(
    profile: str | None = typer.Option(None, "--profile", help="Built-in or custom end-user profile"),
    path: Path | None = typer.Option(None, "--path", help="Existing repository to inspect or initialize"),
    initialize: bool = typer.Option(False, "--initialize", help="Explicitly register this project"),
    interactive: bool = typer.Option(False, "--interactive", help="Prompt for a profile"),
) -> None:
    """Choose an end-user profile; never save secrets or enable model calls.

    Example: ``dbound setup --profile local-safe --path . --initialize``.
    Native CLI and provider availability are reported, not assumed tested.
    """
    from .onboarding import SetupService
    from .profiles import InvalidProfile
    service = SetupService(repository())
    selected = profile or service.profiles.active()
    if interactive:
        typer.echo("Available profiles: " + ", ".join(service.profiles.names()))
        selected = typer.prompt("Select profile", default=selected)
    try:
        _print(service.configure(selected, path, initialize).to_dict())
    except (KeyError, InvalidProfile, ValueError, PermissionError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("doctor")
def doctor(
    profile: str | None = typer.Option(None, "--profile"),
    path: Path | None = typer.Option(None, "--path"),
    strict: bool = typer.Option(False, "--strict", help="Exit nonzero if required prerequisites are missing"),
) -> None:
    """Read-only prerequisite diagnostics with no credential disclosure.

    This does not call provider endpoints or certify Docker/MCP integrations.
    ``--strict`` is suitable for local setup checks and smoke-test jobs.
    """
    from .onboarding import SetupService
    service = SetupService(repository())
    try:
        report = service.inspect(profile, path)
    except KeyError as exc:
        raise typer.BadParameter("Unknown profile") from exc
    _print(report.to_dict())
    if strict and not report.ready:
        raise typer.Exit(2)


@app.command()
def init(path: Path = typer.Option(Path("."), help="Existing project directory"),
         name: str | None = typer.Option(None), owner: str = typer.Option("local"),
         technologies: str = typer.Option("", help="Comma-separated technology tags")) -> None:
    """Initialize a local project identity; never enable cloud services."""
    project = init_project(path, repository(), name, tuple(t.strip().lower() for t in technologies.split(",") if t.strip()), owner)
    _print(asdict(project))


@app.command()
def status(path: Path = typer.Option(Path("."))) -> None:
    """Inspect trusted project identity and active provider profile."""
    store = repository()
    project = load_project(path, store)
    _print({"project": asdict(project), "active_profile": ProfileManager(store).active(),
            "decisions": len(store.list_decisions(project.project_id))})


@profiles_cli.command("list")
def profiles_list() -> None:
    """Show built-in and saved profiles without revealing credentials."""
    manager = ProfileManager(repository())
    _print({"active": manager.active(), "profiles": manager.names()})


@profiles_cli.command("use")
def profiles_use(name: str) -> None:
    """Select the active profile for future tasks in this user's runtime."""
    _print(ProfileManager(repository()).use(name))


@profiles_cli.command("create")
def profiles_create(name: str, extends: str = typer.Option("local-safe", "--from")) -> None:
    """Create a custom profile inheriting a reviewed built-in preset."""
    _print(ProfileManager(repository()).create(name, extends))


@profiles_cli.command("export")
def profiles_export(name: str, output: Path = typer.Option(..., "--output")) -> None:
    """Export a portable, credential-free YAML profile."""
    ProfileManager(repository()).export(name, output)
    typer.echo(str(output))


@profiles_cli.command("import")
def profiles_import(path: Path) -> None:
    """Validate and import a shared YAML profile; no permission escalation."""
    _print(ProfileManager(repository()).import_file(path))


@decisions_cli.command("discover")
def decision_discover(relative_path: str, project_path: Path = typer.Option(Path("."), "--path"),
                      scope: str = typer.Option("project")) -> None:
    """Create an unapproved candidate by reading a local architecture record."""
    store = repository()
    project = load_project(project_path, store)
    decision = DecisionService(store).discover_adr(project, project_path, relative_path, scope)
    _print(decision.to_dict())


@decisions_cli.command("approve")
def decision_approve(decision_id: str, revision: int = typer.Option(1),
                     project_path: Path = typer.Option(Path("."), "--path")) -> None:
    """Approve reviewed evidence only if the source hash is still current."""
    store = repository()
    project = load_project(project_path, store)
    decision = store.get_decision(decision_id)
    if decision is None or decision.source_project_id != project.project_id:
        raise typer.BadParameter("Decision does not belong to this project")
    _print(DecisionService(store).approve(project_path, decision_id, revision).to_dict())


@decisions_cli.command("list")
def decisions_list(project_path: Path = typer.Option(Path("."), "--path")) -> None:
    """List reviewed and unreviewed decisions owned by the current project."""
    store = repository()
    project = load_project(project_path, store)
    _print([d.to_dict() for d in store.list_decisions(project.project_id)])


@app.command("transfer")
def transfer_preview(path: Path = typer.Option(Path("."), "--path")) -> None:
    """Preview selective transfer; never write into target repository."""
    store = repository()
    project = load_project(path, store)
    _print([asdict(result) for result in DecisionService(store).transfer(project)])


@app.command("prepare")
def prepare(task: str, path: Path = typer.Option(Path("."), "--path"),
            tokens: int = typer.Option(1200, "--tokens")) -> None:
    """Create an explainable task capsule under a context-token budget."""
    store = repository()
    project = load_project(path, store)
    capsule = DecisionService(store).prepare(task, project, tokens)
    _print(capsule.to_dict())


@app.command("run")
def run(task: str, path: Path = typer.Option(Path("."), "--path"),
        tokens: int = typer.Option(1200, "--tokens")) -> None:
    """Execute a bounded offline preparation worker and persist its trace."""
    store = repository()
    project = load_project(path, store)
    capsule = DecisionService(store).prepare(task, project, tokens)
    _print(Supervisor(store).run(task, capsule))


@app.command("trace")
def trace(run_id: str) -> None:
    """Inspect the durable event log for a previous run."""
    _print(repository().events(run_id))


@app.command("skills")
def skills(path: Path = typer.Option(Path("."), "--path")) -> None:
    """Discover skill metadata without injecting their full text into context."""
    found = SkillRegistry([path / ".agents" / "skills", path / ".claude" / "skills"]).discover()
    _print([{"name": s.name, "summary": s.summary, "path": str(s.path)} for s in found])


@app.command("serve")
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8765)) -> None:
    """Start a local control API; remote binding is intentionally prohibited."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise typer.BadParameter("Only loopback binding is allowed in alpha")
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Install decisionbound[web] to run the control API") from exc
    uvicorn.run("decisionbound.web:create_app", host=host, port=port, factory=True)


@agent_cli.command("demo")
def agent_demo(task: str, path: Path = typer.Option(Path("."), "--path")) -> None:
    """Run a model-shaped, deterministic tool inspection without keys or network.

    This demonstrates a complete planner → tool → verifier → trace execution,
    but does not constitute a live language model or coding-task completion.
    """
    import json
    from .execution import run_readonly_agent
    from .model_planner import ScriptedModelTransport, Pricing
    search = json.dumps({"kind": "tool", "tool": "search_project", "arguments_json": json.dumps({"query": task[:100]}), "message": ""})
    finish = json.dumps({"kind": "finish", "tool": None, "arguments_json": "{}",
                         "message": "Inspection finished. User task remains unverified."})
    _print(run_readonly_agent(project_path=path, store=repository(), task=task,
                              transport=ScriptedModelTransport([search, finish]), pricing=Pricing(0, 0)))


@agent_cli.command("run")
def agent_run(task: str, model: str = typer.Option(..., "--model"),
              provider: str = typer.Option("openai", "--provider"),
              path: Path = typer.Option(Path("."), "--path"),
              allow_remote: bool = typer.Option(False, "--allow-remote"),
              input_price: float = typer.Option(..., "--input-price", help="USD per 1M input tokens"),
              output_price: float = typer.Option(..., "--output-price", help="USD per 1M output tokens"),
              max_cost: float = typer.Option(0.10, "--max-cost", help="Hard preflight USD cap"),
              max_steps: int = typer.Option(5, "--max-steps"),
              api_key_env: str = typer.Option("", "--api-key-env")) -> None:
    """Opt in to a real provider's structured, read-only tool agent.

    Keys are read only from the specified environment variable. No code edits,
    shell commands, remote tools, retries or multi-agent delegation occur.
    Provider prices must be supplied explicitly, and the run is not marked as
    task-complete merely because the model returns a final answer.
    """
    from .execution import run_readonly_agent
    from .model_planner import Pricing, ModelLimits
    from .providers import AnthropicJSONTransport, OpenAICompatibleJSONTransport
    if not allow_remote:
        raise typer.BadParameter("Pass --allow-remote to explicitly authorize model calls")
    try:
        prices = Pricing(input_price, output_price)
        limits = ModelLimits(max_calls=max_steps, max_cost_usd=max_cost)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if provider == "openai":
        transport = OpenAICompatibleJSONTransport(model=model, enabled=True,
                    api_key_env=api_key_env or "OPENAI_API_KEY")
    elif provider == "anthropic":
        transport = AnthropicJSONTransport(model=model, enabled=True,
                    api_key_env=api_key_env or "ANTHROPIC_API_KEY")
    else:
        raise typer.BadParameter("Supported read-only providers: openai, anthropic")
    _print(run_readonly_agent(project_path=path, store=repository(), task=task,
                              transport=transport, pricing=prices, limits=limits))


@mcp_cli.command("serve")
def mcp_serve(path: Path = typer.Option(Path("."), "--path")) -> None:
    """Run an official-MCP-SDK-backed stdio server, bound to one project.

    Never print CLI output to stdout before starting the MCP server. All
    project data returned to clients is scoped to the verified workspace.
    """
    from .mcp_server import serve_stdio
    serve_stdio(path)



@code_cli.command("inspect")
def code_inspect(patch_file: Path = typer.Option(..., "--patch-file"),
                 path: Path = typer.Option(Path('.'), "--path")) -> None:
    """Read-only patch inspection; report changed paths without modifying Git."""
    from .code_patches import PatchValidator
    from .identity import load_project
    store = repository()
    project = load_project(path, store)
    inspection = PatchValidator().inspect(patch_file.read_text(encoding='utf-8'), path)
    _print({'project_id': project.project_id, 'paths': list(inspection.paths),
            'patch_sha256': inspection.sha256, 'added_lines': inspection.added_lines,
            'removed_lines': inspection.removed_lines, 'write_approved': False})


@code_cli.command("mock-run")
def code_mock_run(task: str, patch_file: Path = typer.Option(..., '--patch-file'),
                  path: Path = typer.Option(Path('.'), '--path'),
                  approve_writes: bool = typer.Option(False, '--approve-writes')) -> None:
    """Demonstrate isolated patch application with a fake test runner.

    This deliberately cannot certify code correctness or task completion.
    No Docker daemon, LLM credentials or network access are involved.
    """
    from .code_execution import PatchFileProposer, run_coding_task
    from .code_sandbox import ScriptedTestRunner
    from .settings import state_dir
    if not approve_writes:
        raise typer.BadParameter('--approve-writes is required even for disposable worktrees')
    outcome = run_coding_task(project_path=path, state_root=state_dir(), repository=repository(),
                              task=task, proposer=PatchFileProposer(patch_file.read_text(encoding='utf-8')),
                              runner=ScriptedTestRunner([0]),
                              test_commands=(('mock', 'not-executed'),), approved_writes=True)
    _print(outcome.to_dict())


@code_cli.command("apply")
def code_apply(task: str, patch_file: Path = typer.Option(..., "--patch-file"),
               path: Path = typer.Option(Path('.'), "--path"),
               test_cmd: list[str] = typer.Option([], "--test-cmd", help="Repeatable command; parsed into argv, not a host shell"),
               approve_writes: bool = typer.Option(False, "--approve-writes"),
               allow_docker: bool = typer.Option(False, "--allow-docker"),
               image: str = typer.Option("", "--image"),
               timeout: int = typer.Option(120, "--timeout")) -> None:
    """Apply a reviewed text patch in an isolated worktree, then Docker-test it.

    Requires both explicit write consent and local sandbox consent. The host
    never executes the test command and never changes the source checkout.
    """
    import shlex
    from .code_execution import PatchFileProposer, run_coding_task
    from .code_sandbox import DockerTestRunner
    from .settings import state_dir
    if not approve_writes or not allow_docker:
        raise typer.BadParameter('Both --approve-writes and --allow-docker are mandatory')
    if not image:
        raise typer.BadParameter('Specify an available, trusted --image (prefer a digest)')
    if not test_cmd:
        raise typer.BadParameter('At least one --test-cmd is required')
    commands = tuple(tuple(shlex.split(x)) for x in test_cmd)
    patch = patch_file.read_text(encoding='utf-8')
    result = run_coding_task(project_path=path, state_root=state_dir(), repository=repository(),
                             task=task, proposer=PatchFileProposer(patch),
                             runner=DockerTestRunner(image), test_commands=commands,
                             approved_writes=True, timeout_seconds=timeout)
    _print(result.to_dict())


@code_cli.command("propose")
def code_propose(task: str, target: list[str] = typer.Option([], "--target"),
                 path: Path = typer.Option(Path('.'), "--path"),
                 provider: str = typer.Option('openai', '--provider'),
                 model: str = typer.Option(..., '--model'),
                 api_key_env: str = typer.Option('', '--api-key-env'),
                 input_price: float = typer.Option(..., '--input-price'),
                 output_price: float = typer.Option(..., '--output-price'),
                 max_cost: float = typer.Option(0.25, '--max-cost'),
                 max_output_tokens: int = typer.Option(2048, '--max-output-tokens'),
                 allow_remote: bool = typer.Option(False, '--allow-remote'),
                 approve_writes: bool = typer.Option(False, '--approve-writes'),
                 allow_docker: bool = typer.Option(False, '--allow-docker'),
                 image: str = typer.Option('', '--image'),
                 test_cmd: list[str] = typer.Option([], '--test-cmd')) -> None:
    """Ask one explicitly authorized provider for a small patch and test it.

    The model receives only files selected with --target. Provider connections
    are opt-in and cost-bounded; its diff is checked by immutable host policy.
    """
    import shlex
    from .code_execution import run_coding_task
    from .code_sandbox import DockerTestRunner
    from .code_provider import ModelPatchProposer
    from .model_planner import ModelLimits, Pricing
    from .providers import AnthropicJSONTransport, OpenAICompatibleJSONTransport
    from .settings import state_dir
    if not all((allow_remote, approve_writes, allow_docker)):
        raise typer.BadParameter('Requires --allow-remote, --approve-writes and --allow-docker')
    if not image or not target or not test_cmd:
        raise typer.BadParameter('Specify --image, at least one --target and --test-cmd')
    if provider == 'openai':
        transport = OpenAICompatibleJSONTransport(model=model, enabled=True,
                        api_key_env=api_key_env or 'OPENAI_API_KEY')
    elif provider == 'anthropic':
        transport = AnthropicJSONTransport(model=model, enabled=True,
                        api_key_env=api_key_env or 'ANTHROPIC_API_KEY')
    else:
        raise typer.BadParameter('Supported proposal providers: openai, anthropic')
    pricing = Pricing(input_price, output_price)
    store = repository()
    project = load_project(path, store)
    approved_context = DecisionService(store).prepare(task, project, 1200)
    proposer = ModelPatchProposer(transport, pricing, ModelLimits(max_calls=1,
                    max_output_tokens_per_call=max_output_tokens, max_cost_usd=max_cost),
                    context=approved_context)
    commands = tuple(tuple(shlex.split(x)) for x in test_cmd)
    result = run_coding_task(project_path=path, state_root=state_dir(), repository=store,
                             task=task, proposer=proposer, runner=DockerTestRunner(image),
                             test_commands=commands, approved_writes=True,
                             source_targets=tuple(target))
    value = result.to_dict()
    value['model_accounted_cost_usd'] = proposer.accounted_cost_usd
    _print(value)


@code_cli.command("export")
def code_export(run_id: str, output: Path = typer.Option(..., '--output'),
                acknowledge_unverified: bool = typer.Option(False, '--acknowledge-unverified')) -> None:
    """Export a SHA-256-verified patch without applying it to original source."""
    from .code_artifacts import export_coding_patch
    from .settings import state_dir
    _print(export_coding_patch(repository=repository(), state_root=state_dir(), run_id=run_id,
                               destination=output, acknowledge_unverified=acknowledge_unverified))


@code_cli.command("pending")
def code_pending() -> None:
    """Show interrupted worktrees requiring cleanup, never retry automatically."""
    from .code_workspace import GitWorktreeManager
    from .settings import state_dir
    _print({'pending': GitWorktreeManager(state_dir()).pending()})


@code_cli.command("cleanup")
def code_cleanup(run_id: str, approve_cleanup: bool = typer.Option(False, '--approve-cleanup')) -> None:
    """Explicitly remove a recorded disposable worktree after review."""
    from .code_workspace import GitWorktreeManager
    from .settings import state_dir
    if not approve_cleanup:
        raise typer.BadParameter('--approve-cleanup is required')
    cleaned = GitWorktreeManager(state_dir()).cleanup(run_id)
    repository().record_event(run_id, 'code_operator_cleanup', {'cleaned': cleaned})
    _print({'run_id': run_id, 'cleaned': cleaned})

def _load_contract_file(path: Path):
    """Load a small explicit YAML/JSON task contract, never model-generated tests."""
    import yaml
    from .code_acceptance import TaskContract
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32_000:
        raise typer.BadParameter('Contract must be a regular file under 32 KB')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    try:
        return TaskContract.from_dict(data)
    except (TypeError, ValueError, KeyError) as exc:
        raise typer.BadParameter(f'Invalid acceptance contract: {exc}') from exc


@code_cli.command('repair-status')
def code_repair_status(session_id: str) -> None:
    """Inspect durable repair checkpoints without exposing prompts or code."""
    from .code_journal import SQLiteRepairJournal
    from .settings import state_dir
    _print(SQLiteRepairJournal(repository(), state_dir()).get(session_id).to_dict())


@code_cli.command('repair-mock')
def code_repair_mock(task: str, contract: Path = typer.Option(..., '--contract'),
                     key: str = typer.Option(..., '--key'),
                     patch_file: list[Path] = typer.Option([], '--patch-file'),
                     test_exit: list[int] = typer.Option([], '--test-exit'),
                     path: Path = typer.Option(Path('.'), '--path'),
                     approve_writes: bool = typer.Option(False, '--approve-writes'),
                     resume: bool = typer.Option(False, '--resume')) -> None:
    """Rehearse a bounded repair loop without LLM calls or real test execution.

    Every attempt applies a separate operator-provided patch in an isolated
    worktree; mock success never certifies real tests or whole-task completion.
    """
    from .code_repair import RepairLimits, ScriptedRepairFactory, run_repair_session
    from .code_sandbox import ScriptedTestRunner
    from .settings import state_dir
    if not approve_writes:
        raise typer.BadParameter('Explicit --approve-writes is mandatory')
    if not patch_file or len(patch_file) > 5:
        raise typer.BadParameter('Provide one to five --patch-file arguments')
    if any(f.is_symlink() or not f.is_file() or f.stat().st_size > 96_000 for f in patch_file):
        raise typer.BadParameter('Invalid patch fixture file')
    approved = _load_contract_file(contract)
    factory = ScriptedRepairFactory(tuple(f.read_text(encoding='utf-8') for f in patch_file))
    runner = ScriptedTestRunner(test_exit or [0] * len(patch_file))
    result = run_repair_session(idempotency_key=key, project_path=path,
        state_root=state_dir(), repository=repository(), task=task, contract=approved,
        proposer_factory=factory, runner=runner,
        limits=RepairLimits(max_attempts=len(patch_file)),
        source_targets=tuple(x for x in approved.required_paths if (path / x).is_file()),
        approved_writes=True, resume=resume)
    _print(result.to_dict())


@code_cli.command('repair-provider')
def code_repair_provider(task: str, contract: Path = typer.Option(..., '--contract'),
                         key: str = typer.Option(..., '--key'),
                         path: Path = typer.Option(Path('.'), '--path'),
                         provider: str = typer.Option('openai', '--provider'),
                         model: str = typer.Option(..., '--model'),
                         source_target: list[str] = typer.Option([], '--source-target'),
                         input_price: float = typer.Option(..., '--input-price'),
                         output_price: float = typer.Option(..., '--output-price'),
                         max_cost: float = typer.Option(..., '--max-cost'),
                         max_attempts: int = typer.Option(2, '--max-attempts'),
                         image: str = typer.Option(..., '--image'),
                         allow_remote: bool = typer.Option(False, '--allow-remote'),
                         allow_docker: bool = typer.Option(False, '--allow-docker'),
                         approve_writes: bool = typer.Option(False, '--approve-writes'),
                         resume: bool = typer.Option(False, '--resume')) -> None:
    """Run a paid, budgeted patch-repair loop with real Docker verification.

    Model transport and Docker execution are both explicitly gated. The
    caller-supplied contract controls paths and tests; model output cannot
    choose a shell command or authorize any writes.
    """
    from .code_repair import RepairLimits, ModelRepairFactory, run_repair_session
    from .code_sandbox import DockerTestRunner
    from .model_planner import Pricing
    from .providers import OpenAICompatibleJSONTransport, AnthropicJSONTransport
    from .settings import state_dir
    if not all((allow_remote, allow_docker, approve_writes)):
        raise typer.BadParameter('Requires --allow-remote, --allow-docker, --approve-writes')
    approved = _load_contract_file(contract)
    if provider == 'openai':
        transport = OpenAICompatibleJSONTransport(model=model, enabled=True)
    elif provider == 'anthropic':
        transport = AnthropicJSONTransport(model=model, enabled=True)
    else:
        raise typer.BadParameter('Supported budgeted providers: openai, anthropic')
    factory = ModelRepairFactory(transport, Pricing(input_price, output_price))
    result = run_repair_session(idempotency_key=key, project_path=path,
        state_root=state_dir(), repository=repository(), task=task, contract=approved,
        proposer_factory=factory, runner=DockerTestRunner(image),
        limits=RepairLimits(max_attempts=max_attempts, max_model_cost_usd=max_cost),
        source_targets=tuple(source_target), approved_writes=True, resume=resume)
    _print(result.to_dict())


@code_cli.command('native-propose')
def code_native_propose(task: str, contract: Path = typer.Option(..., '--contract'),
                        harness: str = typer.Option(..., '--harness'),
                        model: str = typer.Option(..., '--model'),
                        path: Path = typer.Option(Path('.'), '--path'),
                        image: str = typer.Option(..., '--image'),
                        source_target: list[str] = typer.Option([], '--source-target'),
                        max_budget_usd: float = typer.Option(0.5, '--max-budget-usd'),
                        allow_native: bool = typer.Option(False, '--allow-native'),
                        allow_docker: bool = typer.Option(False, '--allow-docker'),
                        approve_writes: bool = typer.Option(False, '--approve-writes'),
                        acknowledge_unmetered: bool = typer.Option(False, '--acknowledge-unmetered')) -> None:
    """Optionally use native Claude/Codex CLI as a *proposal-only* worker.

    Codex CLI costs cannot be strongly capped in this adapter. Its use
    requires a separate acknowledgement; both adapters are unverified with
    real installed hosts until user-run connected tests are complete.
    """
    from .code_native import CodexCLIPatchProposer, ClaudeCLIPatchProposer
    from .code_sandbox import DockerTestRunner
    from .code_execution import run_coding_task
    from .settings import state_dir
    if not all((allow_native, allow_docker, approve_writes)):
        raise typer.BadParameter('Requires --allow-native, --allow-docker, --approve-writes')
    if harness == 'codex':
        proposer = CodexCLIPatchProposer(model=model, enabled=True,
                                         acknowledged_unmetered=acknowledge_unmetered)
    elif harness == 'claude':
        proposer = ClaudeCLIPatchProposer(model=model, enabled=True,
                                          max_budget_usd=max_budget_usd)
    else:
        raise typer.BadParameter('Harness must be codex or claude')
    approved = _load_contract_file(contract)
    outcome = run_coding_task(project_path=path, state_root=state_dir(), repository=repository(),
        task=task, proposer=proposer, runner=DockerTestRunner(image),
        test_commands=approved.test_commands, contract=approved,
        source_targets=tuple(source_target), approved_writes=True)
    _print(outcome.to_dict())



@client_cli.command("pair")
def client_pair(show_token: bool = typer.Option(False, "--show-token")) -> None:
    """Generate a user-local pairing secret; reveal only with explicit consent.

    Never copy the pairing token into repository files or issue tracker logs.
    The extension stores it using VS Code SecretStorage, not workspace settings.
    """
    from .settings import state_dir
    from .local_auth import local_token
    if show_token:
        typer.echo(local_token())
    else:
        _print({"token_location": str(state_dir() / "local_token"),
                "message": "Run dbound client pair --show-token to reveal the secret privately"})


@client_cli.command("capabilities")
def client_capabilities() -> None:
    """Print the versioned local client sync contract without a daemon."""
    from .client_sync import LocalClientSync
    _print(LocalClientSync.capabilities())


@client_cli.command("sessions")
def client_sessions(path: Path = typer.Option(Path('.'), '--path'),
                    limit: int = typer.Option(25, '--limit'),
                    before: int | None = typer.Option(None, '--before')) -> None:
    """List local workflows for the initialized repository, with stable paging."""
    from .client_sync import LocalClientSync
    store = repository()
    project = load_project(path, store)
    _print(LocalClientSync(store).list_sessions(project.project_id, limit=limit, before=before))


@client_cli.command("inspect")
def client_inspect(session_id: str, path: Path = typer.Option(Path('.'), '--path')) -> None:
    """Inspect the same session snapshot as the browser or VS Code client."""
    from .client_sync import LocalClientSync
    store = repository()
    project = load_project(path, store)
    _print(LocalClientSync(store).snapshot(project.project_id, session_id))


@client_cli.command("changes")
def client_changes(path: Path = typer.Option(Path('.'), '--path'),
                   after: int = typer.Option(0, '--after'),
                   limit: int = typer.Option(50, '--limit')) -> None:
    """Continue an ordered metadata-only change feed after the saved cursor."""
    from .client_sync import LocalClientSync
    store = repository()
    project = load_project(path, store)
    _print(LocalClientSync(store).changes(project.project_id, after=after, limit=limit))


def _workflow_file(path: Path):
    """Parse a bounded local YAML/JSON graph without evaluating arbitrary tags."""
    import yaml
    from .workflows import WorkflowSpec
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000:
        raise typer.BadParameter("Workflow manifest must be a regular file below 64 KB")
    try:
        return WorkflowSpec.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@workflow_cli.command("validate")
def workflow_validate(file: Path = typer.Option(..., "--file")) -> None:
    """Validate a versioned DAG and display its immutable manifest fingerprint."""
    import hashlib
    spec = _workflow_file(file)
    data = json.dumps(spec.to_dict(), sort_keys=True)
    _print({"valid": True, "task_count": len(spec.tasks), "spec_sha256": hashlib.sha256(data.encode()).hexdigest(),
            "kinds": sorted({t.kind for t in spec.tasks})})


@workflow_cli.command("submit")
def workflow_submit(file: Path = typer.Option(..., "--file"),
                    path: Path = typer.Option(Path('.'), '--path'),
                    idempotency_key: str | None = typer.Option(None, '--idempotency-key')) -> None:
    """Register a local workflow; do not start paid calls, code writes or workers."""
    from .workflows import WorkflowJournal
    import hashlib
    spec = _workflow_file(file)
    if idempotency_key is None:
        canonical = json.dumps({"root": str(path.resolve()), "spec": spec.to_dict()}, sort_keys=True)
        idempotency_key = "graph_" + hashlib.sha256(canonical.encode()).hexdigest()[:32]
    store = repository()
    wf = WorkflowJournal(store).create(project_path=path, spec=spec, idempotency_key=idempotency_key)
    _print(WorkflowJournal(store).status(wf))


@workflow_cli.command("run")
def workflow_run(workflow_id: str,
                 lease_seconds: int = typer.Option(120, '--lease-seconds'),
                 max_wall_seconds: int = typer.Option(120, '--max-wall-seconds')) -> None:
    """Dispatch registered offline preparation and synthetic test workers only.

    Code-writing workers require separate explicit dependency injection by the
    SDK and cannot be enabled through this convenience command.
    """
    from .workflows import WorkflowJournal, WorkflowSupervisor
    store = repository()
    _print(WorkflowSupervisor(WorkflowJournal(store)).run(
        workflow_id, lease_seconds=lease_seconds, max_wall_seconds=max_wall_seconds))


@workflow_cli.command("leader-status")
def workflow_leader_status() -> None:
    """Read the local background coordinator health without exposing tokens."""
    from .background import LocalCoordinatorLeader
    _print(LocalCoordinatorLeader(repository()).status())


@workflow_cli.command("serve")
def workflow_serve(
    path: Path = typer.Option(Path('.'), '--path', help='Existing initialized project'),
    once: bool = typer.Option(False, '--once', help='Perform one scheduling cycle and exit'),
    max_seconds: float | None = typer.Option(None, '--max-seconds', help='Bound service scheduling lifetime'),
    poll_seconds: float = typer.Option(2.0, '--poll-seconds'),
    max_rss_mib: int = typer.Option(512, '--max-rss-mib'),
    allow_mock: bool = typer.Option(False, '--allow-mock', help='Explicitly enable synthetic mock stages'),
    low_resource: bool = typer.Option(False, '--low-resource', help='One task at a time, conservative memory and idle polling'),
    contained: bool = typer.Option(False, '--contained', help='Run preparation in a killable child process with a deadline'),
) -> None:
    """Run an opt-in, restartable, single-device workflow coordinator.

    Only offline preparation workers are enabled by default. Agent/provider
    execution, shell, Docker, and coding writes require separately injected
    consented SDK workers. Install this command under a supervised OS service
    for operation over days; a chat session does not launch background jobs.

    With ``--contained`` each preparation runs in a child process that is killed
    at its deadline (120 seconds), on cancel, and after a coordinator crash. It
    refuses to start on a host that cannot prove termination of the process tree.
    """
    import signal
    import threading
    from .background import BackgroundCoordinator, CoordinatorSettings, CoordinatorConflict, ResourceBudgetExceeded
    from .workflows import PreparationWorker, ScriptedWorkflowWorker
    store = repository()
    project = load_project(path, store)
    stop = threading.Event()
    original_handlers = {}

    def request_stop(signum, frame):
        """Request graceful drain; verified task ownership stays host-fenced."""
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)
    try:
        if contained:
            from .containment import ProcessRegistry
            from .worker_child import contained_preparation_worker
            workers = {'prepare': contained_preparation_worker(ProcessRegistry(store), deadline_seconds=120)}
        else:
            workers = {'prepare': PreparationWorker(store)}
        if allow_mock:
            workers['mock'] = ScriptedWorkflowWorker()
        settings = CoordinatorSettings(
            poll_seconds=max(5.0, poll_seconds) if low_resource else poll_seconds,
            max_rss_mib=min(256, max_rss_mib) if low_resource else max_rss_mib,
            max_workflows_per_cycle=2 if low_resource else 8,
            max_dispatch_per_workflow=1 if low_resource else 2)
        runner = BackgroundCoordinator(store, project_id=project.project_id,
                                       settings=settings, workers=workers)
        _print(runner.run(stop_event=stop, max_seconds=max_seconds, once=once))
    except (CoordinatorConflict, ResourceBudgetExceeded) as exc:
        # Do not leak worker output or process secrets into CLI errors.
        typer.echo(f'Coordinator stopped: {type(exc).__name__}', err=True)
        raise typer.Exit(2) from exc
    finally:
        for sig, original in original_handlers.items():
            signal.signal(sig, original)


@workflow_cli.command("reap-orphans")
def workflow_reap_orphans(
    term_grace_seconds: float = typer.Option(3.0, '--term-grace-seconds', min=0, max=60,
                                             help='Seconds between SIGTERM and SIGKILL'),
) -> None:
    """Kill contained worker trees left behind by a dead coordinator.

    Takes the exclusive coordinator lease for the duration, so it cannot race a
    running coordinator. Only processes whose recorded start token still matches
    are signalled. Prints counts only.
    """
    from .background import CoordinatorConflict, LocalCoordinatorLeader
    from .containment import OrphanReaper, ProcessRegistry
    from .workflows import WorkflowJournal
    store = repository()
    leader = LocalCoordinatorLeader(store)
    try:
        owned = leader.acquire(ttl_seconds=60)
    except CoordinatorConflict as exc:
        typer.echo('A coordinator is running; stop it or let it reap on its own', err=True)
        raise typer.Exit(2) from exc
    try:
        report = OrphanReaper(ProcessRegistry(store, create=False), WorkflowJournal(store),
                              term_grace_seconds=term_grace_seconds).reap(leader_generation=owned.generation)
    finally:
        leader.release(owned)
    _print(report)


@workflow_cli.command("supervised-code")
def workflow_supervised_code(
    task: str,
    contract: Path = typer.Option(..., '--contract'),
    patch_file: list[Path] = typer.Option([], '--patch-file'),
    path: Path = typer.Option(Path('.'), '--path'),
    key: str = typer.Option(..., '--key'),
    source_target: list[str] = typer.Option([], '--source-target'),
    mode: str = typer.Option('mock', '--mode'),
    docker_image: str | None = typer.Option(None, '--docker-image'),
    allow_docker: bool = typer.Option(False, '--allow-docker'),
    approve_writes: bool = typer.Option(False, '--approve-writes'),
    require_red_test: bool = typer.Option(False, '--require-red-test'),
) -> None:
    """Execute a fully journalled prepare/code/review DAG under operator approval.

    Offline `mock` exercises state transitions without claiming real tests.
    The `docker` mode requires an approved, pre-pulled image and explicit
    Docker permission. No patch is merged, committed, pushed or exported.
    """
    from .supervised_coding import SupervisedCodingOrchestrator, SupervisedCodingRequest
    from .code_repair import ScriptedRepairFactory, RepairLimits
    from .code_sandbox import DockerTestRunner, ScriptedTestRunner
    from .settings import state_dir
    if approve_writes is not True:
        raise typer.BadParameter('Explicit --approve-writes is required')
    if mode not in {'mock', 'docker'}:
        raise typer.BadParameter('--mode must be mock or docker')
    if not patch_file or len(patch_file) > 5 or any(
        f.is_symlink() or not f.is_file() or f.stat().st_size > 96_000 for f in patch_file
    ):
        raise typer.BadParameter('Provide one to five regular --patch-file inputs')
    if mode == 'docker' and (not allow_docker or not docker_image):
        raise typer.BadParameter('Docker mode requires --allow-docker and --docker-image')
    if mode == 'mock' and (allow_docker or docker_image is not None):
        raise typer.BadParameter('Docker flags are invalid in mock mode')
    approved = _load_contract_file(contract)
    factory = ScriptedRepairFactory(tuple(f.read_text(encoding='utf-8') for f in patch_file))
    runner = DockerTestRunner(docker_image) if mode == 'docker' else ScriptedTestRunner([0] * 5 * len(patch_file))
    request = SupervisedCodingRequest(task, approved, tuple(source_target),
              limits=RepairLimits(max_attempts=len(patch_file), max_model_cost_usd=0),
              require_red_test=require_red_test)
    result = SupervisedCodingOrchestrator(repository=repository(), state_root=state_dir(),
                                         runner=runner, proposer_factory=factory).execute(
        project_path=path, request=request, idempotency_key=key,
        approved_writes=True)
    _print(result)


@workflow_cli.command("status")
def workflow_status(workflow_id: str) -> None:
    """Inspect one workflow and its source-free task states."""
    from .workflows import WorkflowJournal
    _print(WorkflowJournal(repository()).status(workflow_id))


@workflow_cli.command("attest")
def workflow_attest(workflow_id: str) -> None:
    """Recheck current private patch integrity without executing any task."""
    from .settings import state_dir
    from .workflows import WorkflowJournal
    _print(WorkflowJournal(repository()).attest(workflow_id, state_root=state_dir()))


@workflow_cli.command("events")
def workflow_events(workflow_id: str) -> None:
    """Read durable orchestration receipts, never raw task instructions."""
    from .workflows import WorkflowJournal
    _print(WorkflowJournal(repository()).events(workflow_id))


@workflow_cli.command("reconcile")
def workflow_reconcile(workflow_id: str) -> None:
    """Reconcile expired leases without replaying uncertain writes."""
    from .workflows import WorkflowJournal
    _print(WorkflowJournal(repository()).reconcile(workflow_id))


@workflow_cli.command("cancel")
def workflow_cancel(workflow_id: str) -> None:
    """Revoke future work; does not terminate an already running child process."""
    from .workflows import WorkflowJournal
    _print(WorkflowJournal(repository()).cancel(workflow_id))



@review_cli.command("inspect")
def review_inspect(workflow_id: str, show_diff: bool = typer.Option(False, "--show-diff")) -> None:
    """Inspect a sealed coding artifact; source text is opt-in, never logged."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    _print(ReviewGate(repository(), state_dir()).snapshot(workflow_id, include_diff=show_diff))


@review_cli.command("approve")
def review_approve(workflow_id: str,
                   fingerprint: str = typer.Option(..., "--fingerprint"),
                   revision: int = typer.Option(0, "--revision"),
                   acknowledge_unverified: bool = typer.Option(False, "--acknowledge-unverified"),
                   confirm: bool = typer.Option(False, "--confirm-export-approval")) -> None:
    """Approve ONLY export of exactly these patch bytes; never merges or pushes."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    if confirm is not True:
        raise typer.BadParameter("Explicit --confirm-export-approval is required")
    _print(ReviewGate(repository(), state_dir()).decide(workflow_id,
        expected_fingerprint=fingerprint, expected_revision=revision,
        action="approve_export", acknowledge_unverified=acknowledge_unverified))


@review_cli.command("reject")
def review_reject(workflow_id: str, fingerprint: str = typer.Option(..., "--fingerprint"),
                  revision: int = typer.Option(0, "--revision")) -> None:
    """Reject the currently inspected patch; no approval is granted."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    _print(ReviewGate(repository(), state_dir()).decide(workflow_id,
        expected_fingerprint=fingerprint, expected_revision=revision, action="reject"))


@review_cli.command("revoke")
def review_revoke(workflow_id: str, fingerprint: str = typer.Option(..., "--fingerprint"),
                  revision: int = typer.Option(..., "--revision")) -> None:
    """Revoke a previously granted local export approval."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    _print(ReviewGate(repository(), state_dir()).decide(workflow_id,
        expected_fingerprint=fingerprint, expected_revision=revision, action="revoke"))


@review_cli.command("history")
def review_history(workflow_id: str) -> None:
    """List metadata-only approval, rejection, revocation and export events."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    _print(ReviewGate(repository(), state_dir()).history(workflow_id))


@review_cli.command("export")
def review_export(workflow_id: str, destination: Path = typer.Option(..., "--output"),
                  fingerprint: str = typer.Option(..., "--fingerprint"),
                  revision: int = typer.Option(..., "--revision")) -> None:
    """Export an approved patch outside the checkout, with no auto-application."""
    from .review_gate import ReviewGate
    from .settings import state_dir
    _print(ReviewGate(repository(), state_dir()).export(workflow_id, destination=destination,
        expected_fingerprint=fingerprint, expected_revision=revision))


@release_cli.command("verify")
def release_verify(
    manifest: Path = typer.Option(..., "--manifest"),
    signature: Path = typer.Option(..., "--signature"),
    public_key: Path = typer.Option(..., "--trusted-public-key"),
    artifacts_dir: Path = typer.Option(..., "--artifacts-dir"),
    installed_version: str = typer.Option(..., "--installed-version"),
    minimum_accepted: str | None = typer.Option(None, "--minimum-accepted-version"),
) -> None:
    """Verify a pinned Ed25519 release; never download, install or execute it.

    The trusted public key must be provisioned independently of untrusted
    release metadata. Signature validity is necessary, not sufficient, for
    a safe automated rollout (which this command does not implement).
    """
    from .release_trust import ReleaseTrustError, verify_signed_release
    try:
        _print(verify_signed_release(manifest, signature, public_key, artifacts_dir,
                installed_version=installed_version,
                minimum_accepted_version=minimum_accepted))
    except ReleaseTrustError as exc:
        raise typer.BadParameter(str(exc)) from exc


@state_cli.command("check")
def state_check(full: bool = typer.Option(False, "--full", help="Thorough SQLite integrity check"),
                database: Path | None = typer.Option(None, "--database", help="Explicit database file; defaults to local user state")) -> None:
    """Inspect schema, FK integrity and event counts without mutating state.

    A failed check exits nonzero; it does not attempt automatic recovery.
    """
    from .settings import state_dir
    from .state_integrity import StateIntegrityError, inspect_database
    location = database or state_dir() / "state.sqlite3"
    try:
        report = inspect_database(location, full=full)
    except StateIntegrityError as exc:
        _print({"ok": False, "error": str(exc)})
        raise typer.Exit(2) from exc
    _print(report.to_dict())
    if not report.ok:
        raise typer.Exit(2)


@state_cli.command("migrate")
def state_migrate(
    source: Path = typer.Option(..., "--source", help="Existing offline legacy SQLite file"),
    output: Path = typer.Option(..., "--output", help="New upgraded copy; must not exist"),
) -> None:
    """Prepare a separate verified v1→v2 state copy; never mutate active data.

    Stop older DecisionBound processes before switching to the new copy.
    No files are overwritten and this command never activates the migration.
    """
    from .state_migration import migrate_database, StateMigrationError
    from .state_integrity import StateDestinationError, StateIntegrityError
    try:
        _print(migrate_database(source, output).to_dict())
    except (StateMigrationError, StateDestinationError, StateIntegrityError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@state_cli.command("backup")
def state_backup(output: Path = typer.Option(..., "--output", help="New, private snapshot filename")) -> None:
    """Create a consistent online snapshot; never overwrite existing backups.

    The snapshot contains sensitive local data and must not be committed to Git.
    """
    from .settings import state_dir
    from .state_integrity import StateIntegrityError, StateDestinationError, create_backup
    try:
        snapshot = create_backup(state_dir() / "state.sqlite3", output)
    except (StateIntegrityError, StateDestinationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print(snapshot.to_dict())


@state_cli.command("restore")
def state_restore(backup: Path = typer.Option(..., "--backup", help="Validated snapshot filename"),
                  destination: Path = typer.Option(..., "--destination", help="New recovered DB file, not active state")) -> None:
    """Recover to a *new* file only; never replace a running live database.

    Manual migration/reconciliation into active state requires a separate,
    operator-controlled procedure with the runtime stopped.
    """
    from .settings import state_dir
    from .state_integrity import StateIntegrityError, StateDestinationError, restore_to_new_path
    try:
        snapshot = restore_to_new_path(backup, destination, active_path=state_dir() / "state.sqlite3")
    except (StateIntegrityError, StateDestinationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print(snapshot.to_dict())

def main() -> None:
    """Entry point declared by the Python distribution."""
    app()


if __name__ == "__main__":
    main()
