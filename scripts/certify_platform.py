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
    assert len(token) >= 16
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
            assert exc.code == 401, "Unauthenticated session access was not rejected"
        else:
            raise AssertionError("Unauthenticated session access unexpectedly succeeded")
        output = _run(["node", str(code_root / "scripts" / "certify_node_client.cjs")],
                      cwd=repo, env=local, timeout=20)
        response = json.loads(output)
        assert response == {"connected": True, "read_only": True, "task_completed": False}
        assert token not in output, "Local pairing token appeared in stdout"
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
    assert installed is not None and installed.origin
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
        assert not initial["ready"] and initial["project_registered"] is False
        checks.append("strict_uninitialized_project")

        setup = _json_cli(["setup", "--profile", "local-safe", "--path", str(repo), "--initialize"],
                          cwd=repo, env=env)
        assert setup["ready"] is True and setup["integration_tested"] is False
        assert setup["project_registered"] is True
        project = _json_cli(["status", "--path", str(repo)], cwd=repo, env=env)
        project_id = project["project"]["project_id"]
        assert project_id.startswith("prj_") and project["active_profile"] == "local-safe"
        checks.append("project_setup_and_state_isolation")
        assert (state / "state.sqlite3").is_file(), "State was not created in the private directory"
        check = _json_cli(["doctor", "--path", str(repo), "--strict"], cwd=repo, env=env)
        assert check["ready"] and check["project_registered"]
        checks.append("strict_ready_diagnostics")

        capsule = _json_cli(["prepare", "Inspect the local README", "--path", str(repo)],
                            cwd=repo, env=env)
        assert capsule["max_tokens"] > 0
        run = _json_cli(["run", "Inspect the local README", "--path", str(repo)], cwd=repo, env=env)
        assert run["preparation_verified"] and run["task_completed"] is False
        checks.append("offline_run_avoids_completion_claim")
        agent = _json_cli(["agent", "demo", "README", "--path", str(repo)], cwd=repo, env=env)
        # The deterministic model-shaped agent must not prove task completion.
        assert agent.get("task_completed", False) is False
        checks.append("bounded_mock_agent")

        _json_cli(["profile", "create", "os-cert-custom", "--from", "local-safe"], cwd=repo, env=env)
        _json_cli(["profile", "use", "os-cert-custom"], cwd=repo, env=env)
        selected = _json_cli(["profile", "list"], cwd=repo, env=env)
        assert selected["active"] == "os-cert-custom"
        checks.append("custom_profile_switch")
        _json_cli(["setup", "--profile", "local-safe", "--path", str(repo)], cwd=repo, env=env)

        spec_file = root / "workflow.json"
        spec_file.write_text(json.dumps({"schema_version": 1, "tasks": [{"task_id": "inspect",
                                    "kind": "prepare", "description": "Inspect fixture"}]}), encoding="utf-8")
        workflow = _json_cli(["workflow", "submit", "--file", str(spec_file), "--path", str(repo)],
                             cwd=repo, env=env)
        workflow_id = workflow["workflow_id"]
        assert workflow_id.startswith("wf_") and workflow["task_completed"] is False
        staged = _json_cli(["workflow", "run", workflow_id], cwd=repo, env=env)
        assert staged["task_completed"] is False
        sessions = _json_cli(["client", "sessions", "--path", str(repo)], cwd=repo, env=env)
        assert any(item["session_id"] == workflow_id for item in sessions["sessions"])
        checks.append("workflow_and_client_sessions")
        # Exercise recovery using the installed wheel, not the editable tree.
        # Active state is never replaced; recovery creates a separately named file.
        backup = root / "state-backup.db"
        recovered = root / "state-recovered.db"
        health = _json_cli(["state", "check", "--full"], cwd=repo, env=env)
        assert health["ok"] and health["required_tables_present"]
        snapshot = _json_cli(["state", "backup", "--output", str(backup)], cwd=repo, env=env)
        assert snapshot["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
        assert _json_cli(["state", "check", "--database", str(backup)], cwd=repo, env=env)["ok"]
        restored = _json_cli(["state", "restore", "--backup", str(backup),
                               "--destination", str(recovered)], cwd=repo, env=env)
        assert restored["sha256"] == hashlib.sha256(recovered.read_bytes()).hexdigest()
        assert _json_cli(["state", "check", "--database", str(recovered)], cwd=repo, env=env)["ok"]
        assert (state / "state.sqlite3").is_file()  # original never replaced
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
        assert result["source_version"] == 1 and result["target_version"] == 2
        assert hashlib.sha256(legacy.read_bytes()).hexdigest() == source_digest
        migrated_health = _json_cli(["state", "check", "--database", str(upgraded),
                                     "--full"], cwd=repo, env=env)
        assert migrated_health["ok"] and migrated_health["schema_columns_valid"]
        with closing(sqlite3.connect(upgraded)) as migrated:
            stamp = migrated.execute("SELECT created_at FROM run_events WHERE run_id='historic'").fetchone()
            assert stamp == ("unknown",)
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
