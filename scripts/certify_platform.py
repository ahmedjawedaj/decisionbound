"""Certify an *installed* DecisionBound distribution on the host OS.

Unlike in-process pytest or a simulated CLI, this runs the installed console
script in a new repository and private state directory. It is safe for the
Windows, macOS and Linux GitHub-hosted runners. No models, network providers,
Docker, workspace writes or paid calls are authorized. `--http-node` opts in
to a real loopback Uvicorn service and the extension's unmocked Node transport.

Run only after `pip install dist/decisionbound-*.whl` into the selected Python.
This script uses the standard library and requires Git; --http-node additionally
requires locally installed FastAPI/Uvicorn and Node 22+.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _expect(condition: object, message: str) -> None:
    """Fail the certificate when ``condition`` is falsy.

    Not an ``assert`` statement: ``python -O`` removes those, including any CLI call
    inside them, and the report would still list the check as passed.
    """
    if not condition:
        raise AssertionError(message)


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], expect: int = 0,
         timeout: int = 30) -> str:
    """Run a bounded child process; suppress secrets in failure diagnostics."""
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if result.returncode != expect:
        # Never echo stderr: it could contain source data or secret-bearing URLs.
        raise AssertionError(f"Command failed (exit {result.returncode}, expected {expect}): "
                             f"{argv[0]} {argv[1] if len(argv)>1 else ''}")
    return result.stdout


def _json_cli(cmd: list[str], *, cwd: Path, env: dict[str, str], expect: int = 0) -> dict:
    """Execute the installed CLI and decode its stable JSON contract."""
    output = _run(["dbound", *cmd], cwd=cwd, env=env, expect=expect)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AssertionError("Installed CLI did not emit valid JSON") from exc
    if not isinstance(value, dict):
        raise AssertionError("Expected CLI result object")
    return value


def _await_server(proc: subprocess.Popen, url: str, token: str) -> None:
    """Check the real Uvicorn process without assuming a fixed startup delay."""
    for _ in range(120):
        if proc.poll() is not None:
            raise AssertionError("Uvicorn exited before the client connected")
        try:
            with urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=.4) as response:
                if response.status == 200:
                    return
        except (OSError, HTTPError):
            time.sleep(.08)
    raise AssertionError("Uvicorn did not become reachable on loopback")


def _real_http_node(repo: Path, state: Path, env: dict[str, str], project_id: str,
                    workflow_id: str, code_root: Path) -> None:
    """Exercise the actual extension Node transport against the installed API."""
    if shutil.which("node") is None:
        raise AssertionError("Node is required for --http-node")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = _run(["dbound", "client", "pair", "--show-token"], cwd=repo, env=env).strip()
    _expect(len(token) >= 16, "Pairing token was shorter than 16 characters")
    local = dict(env, DBOUND_CERT_PORT=str(port), DBOUND_CERT_TOKEN=token,
                 DBOUND_CERT_PROJECT=project_id, DBOUND_CERT_WORKFLOW=workflow_id)
    url = f"http://127.0.0.1:{port}/api/client/v1/capabilities"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "decisionbound.web:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "critical"],
        cwd=repo, env=local, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _await_server(server, url, token)
        # The endpoint cannot be used without the local pairing token.
        try:
            urlopen(url, timeout=1)
        except HTTPError as exc:
            _expect(exc.code == 401, "Unauthenticated session access was not rejected")
        else:
            raise AssertionError("Unauthenticated session access unexpectedly succeeded")
        output = _run(["node", str(code_root / "scripts" / "certify_node_client.cjs")],
                      cwd=repo, env=local, timeout=20)
        response = json.loads(output)
        _expect(response == {"connected": True, "read_only": True, "task_completed": False},
                "Node client did not report a connected, read-only, not-completed session")
        _expect(token not in output, "Local pairing token appeared in stdout")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def certify(*, http_node: bool = False) -> dict:
    """Run an OS-level smoke certificate and return credential-free results."""
    if shutil.which("dbound") is None:
        raise AssertionError("Install the built DecisionBound wheel before certification")
    if shutil.which("git") is None:
        raise AssertionError("Git is required for this certification")
    import importlib.util
    installed = importlib.util.find_spec("decisionbound")
    _expect(installed is not None and installed.origin, "decisionbound is not importable")
    code_root = Path(__file__).resolve().parents[1]
    # This guard detects accidentally testing the working tree rather than an
    # installed distribution. VS Code sources are read separately for Node.
    if Path(installed.origin).resolve().is_relative_to((code_root / "src").resolve()):
        raise AssertionError("Testing imported source tree, not the installed wheel")

    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dbound-os-cert-") as temp:
        root = Path(temp)
        state = root / "private_state"
        state.mkdir()
        repo = root / "example_repo"
        repo.mkdir()
        env = os.environ.copy()
        env["DBOUND_HOME"] = str(state)
        env["DBOUND_CERT_CANARY"] = "should-not-appear-in-any-output-9cc82ac"
        # Never let a caller's development PYTHONPATH point back at src/.
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in env.get("PYTHONPATH", "").split(os.pathsep)
            if p and (code_root / "src").resolve() != Path(p).resolve())
        _run(["git", "init", "-q"], cwd=repo, env=env)
        (repo / "README.md").write_text("# Real wheel installation fixture\n", encoding="utf-8")
        original_sha = hashlib.sha256((repo / "README.md").read_bytes()).hexdigest()
        initial = _json_cli(["doctor", "--path", str(repo), "--strict"], cwd=repo, env=env, expect=2)
        _expect(not initial["ready"] and initial["project_registered"] is False,
                "Uninitialized project was reported ready or registered")
        checks.append("strict_uninitialized_project")

        setup = _json_cli(["setup", "--profile", "local-safe", "--path", str(repo), "--initialize"],
                          cwd=repo, env=env)
        _expect(setup["ready"] is True and setup["integration_tested"] is False,
                "Setup did not report ready with integration untested")
        _expect(setup["project_registered"] is True, "Setup did not register the project")
        project = _json_cli(["status", "--path", str(repo)], cwd=repo, env=env)
        project_id = project["project"]["project_id"]
        _expect(project_id.startswith("prj_") and project["active_profile"] == "local-safe",
                "Project id or active profile is wrong")
        checks.append("project_setup_and_state_isolation")
        _expect((state / "state.sqlite3").is_file(), "State was not created in the private directory")
        check = _json_cli(["doctor", "--path", str(repo), "--strict"], cwd=repo, env=env)
        _expect(check["ready"] and check["project_registered"], "Strict doctor did not report ready")
        checks.append("strict_ready_diagnostics")

        capsule = _json_cli(["prepare", "Inspect the local README", "--path", str(repo)],
                            cwd=repo, env=env)
        _expect(capsule["max_tokens"] > 0, "Prepared capsule has no token budget")
        run = _json_cli(["run", "Inspect the local README", "--path", str(repo)], cwd=repo, env=env)
        _expect(run["preparation_verified"] and run["task_completed"] is False,
                "Offline run was unverified or claimed completion")
        checks.append("offline_run_avoids_completion_claim")
        agent = _json_cli(["agent", "demo", "README", "--path", str(repo)], cwd=repo, env=env)
        # The deterministic model-shaped agent must not prove task completion.
        _expect(agent["task_completed"] is False, "Mock agent claimed completion or omitted task_completed")
        checks.append("bounded_mock_agent")

        _json_cli(["profile", "create", "os-cert-custom", "--from", "local-safe"], cwd=repo, env=env)
        _json_cli(["profile", "use", "os-cert-custom"], cwd=repo, env=env)
        selected = _json_cli(["profile", "list"], cwd=repo, env=env)
        _expect(selected["active"] == "os-cert-custom", "Profile switch did not take effect")
        checks.append("custom_profile_switch")
        _json_cli(["setup", "--profile", "local-safe", "--path", str(repo)], cwd=repo, env=env)

        spec_file = root / "workflow.json"
        spec_file.write_text(json.dumps({"schema_version": 1, "tasks": [{"task_id": "inspect",
                                    "kind": "prepare", "description": "Inspect fixture"}]}), encoding="utf-8")
        workflow = _json_cli(["workflow", "submit", "--file", str(spec_file), "--path", str(repo)],
                             cwd=repo, env=env)
        workflow_id = workflow["workflow_id"]
        _expect(workflow_id.startswith("wf_") and workflow["task_completed"] is False,
                "Workflow submit returned a bad id or claimed completion")
        staged = _json_cli(["workflow", "run", workflow_id], cwd=repo, env=env)
        _expect(staged["task_completed"] is False, "Workflow run claimed completion")
        sessions = _json_cli(["client", "sessions", "--path", str(repo)], cwd=repo, env=env)
        _expect(any(item["session_id"] == workflow_id for item in sessions["sessions"]),
                "Workflow is missing from client sessions")
        checks.append("workflow_and_client_sessions")
        # Exercise recovery using the installed wheel, not the editable tree.
        # Active state is never replaced; recovery creates a separately named file.
        backup = root / "state-backup.db"
        recovered = root / "state-recovered.db"
        health = _json_cli(["state", "check", "--full"], cwd=repo, env=env)
        _expect(health["ok"] and health["required_tables_present"], "Live state failed its full check")
        snapshot = _json_cli(["state", "backup", "--output", str(backup)], cwd=repo, env=env)
        _expect(snapshot["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest(),
                "Backup digest does not match the file")
        backup_health = _json_cli(["state", "check", "--database", str(backup)], cwd=repo, env=env)
        _expect(backup_health["ok"], "Backup failed its integrity check")
        restored = _json_cli(["state", "restore", "--backup", str(backup),
                               "--destination", str(recovered)], cwd=repo, env=env)
        _expect(restored["sha256"] == hashlib.sha256(recovered.read_bytes()).hexdigest(),
                "Restored digest does not match the file")
        recovered_health = _json_cli(["state", "check", "--database", str(recovered)], cwd=repo, env=env)
        _expect(recovered_health["ok"], "Restored state failed its integrity check")
        _expect((state / "state.sqlite3").is_file(), "Restore replaced or removed the live state")
        checks.append("atomic_backup_integrity_and_non_overwriting_restore")
        # A real v1 compatibility drill via the installed console script.
        # The source must remain byte-identical; the copied v2 state carries
        # the historical record and an explicitly unknown creation timestamp.
        legacy = root / "legacy-v1.db"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute("CREATE TABLE run_events (id INTEGER PRIMARY KEY, run_id TEXT, event TEXT, payload TEXT)")
            db.execute("INSERT INTO run_events VALUES (1,'historic','created','{}')")
            db.execute("PRAGMA user_version=1")
        source_digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
        upgraded = root / "migrated-v2.db"
        result = _json_cli(["state", "migrate", "--source", str(legacy),
                            "--output", str(upgraded)], cwd=repo, env=env)
        _expect(result["source_version"] == 1 and result["target_version"] == 2,
                "Migration reported the wrong schema versions")
        _expect(hashlib.sha256(legacy.read_bytes()).hexdigest() == source_digest,
                "Migration modified its source database")
        migrated_health = _json_cli(["state", "check", "--database", str(upgraded),
                                     "--full"], cwd=repo, env=env)
        _expect(migrated_health["ok"] and migrated_health["schema_columns_valid"],
                "Migrated state failed its full check")
        with closing(sqlite3.connect(upgraded)) as migrated:
            stamp = migrated.execute("SELECT created_at FROM run_events WHERE run_id='historic'").fetchone()
            _expect(stamp == ("unknown",), "Migrated history did not carry an unknown timestamp")
        checks.append("legacy_state_migration_does_not_overwrite_original")
        if http_node:
            _real_http_node(repo, state, env, project_id, workflow_id, code_root)
            checks.append("real_uvicorn_node_extension_transport")

        if hashlib.sha256((repo / "README.md").read_bytes()).hexdigest() != original_sha:
            raise AssertionError("Offline certification unexpectedly edited repository source")
        checks.append("source_checkout_unchanged")
        # The state store and manifest are deliberately ephemeral: no live
        # credentials, external services, or project data are retained.
    return {"platform": platform.platform(), "os": platform.system(),
            "python": platform.python_version(), "package": "decisionbound",
            "package_source": "installed-distribution", "checks_passed": checks,
            "real_http_node": http_node, "external_providers_tested": False,
            "vscode_host_tested": False, "docker_tested": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-node", action="store_true", help="Test Node extension transport to real loopback Uvicorn")
    args = parser.parse_args()
    try:
        report = certify(http_node=args.http_node)
        print(json.dumps(report, sort_keys=True, indent=2))
    except (AssertionError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        # No raw command output or secrets in failures.
        print(json.dumps({"certification": "failed", "reason": type(error).__name__ + ": " + str(error)}),
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
