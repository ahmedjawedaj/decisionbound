"""Host-certification harness checks independent of installed vendor binaries."""
from __future__ import annotations
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _cert_module():
    spec = importlib.util.spec_from_file_location("decisionbound_cert_platform", ROOT / "scripts" / "certify_platform.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_subprocess_does_not_echo_secret_stderr(tmp_path):
    """A failed host probe must not print even a deliberately malicious child stderr."""
    certify = _cert_module()
    marker = "PRIVATE_LOCAL_CERT_SECRET_DO_NOT_PRINT"
    with pytest.raises(AssertionError) as error:
        certify._run([sys.executable, "-c",
                      f"import sys;sys.stderr.write({marker!r});sys.exit(3)"],
                     cwd=tmp_path, env=os.environ.copy())
    assert marker not in str(error.value)
    assert "exit 3" in str(error.value)


def test_host_matrix_covers_real_windows_linux_macos_and_vscode():
    """The matrix must run real installed wheels and real VS Code host downloads."""
    spec = yaml.safe_load((ROOT / ".github/workflows/platform-certification.yml").read_text())
    jobs = spec["jobs"]
    install = jobs["installed-cli"]
    assert set(install["strategy"]["matrix"]["os"]) == {
        "ubuntu-latest", "windows-latest", "macos-latest"}
    commands = "\n".join(str(step.get("run", "")) for step in install["steps"])
    assert "pip wheel" in commands and "install_built_wheel.py --no-deps" in commands
    installer = (ROOT / "scripts/install_built_wheel.py").read_text()
    assert "--no-index" in installer and "--no-deps" in installer
    assert "wheel_path()" in installer and "tomllib" in installer
    assert "certify_platform.py --http-node" in commands
    assert "pip install -e" not in commands, "Certification must install a built wheel"
    host = jobs["vscode-real-host-cross-os"]
    assert set(host["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    host_cmds = "\n".join(str(step.get("run", "")) for step in host["steps"])
    assert "test:host" in host_cmds and "package:vsix" in host_cmds
    assert "@vscode/test-electron" in host_cmds


def test_node_probe_uses_real_loopback_transport_and_no_write_actions():
    """The host probe should call the same read-only extension transport."""
    source = (ROOT / "scripts/certify_node_client.cjs").read_text()
    assert "LocalControlClient" in source and ".sessions(" in source
    assert ".snapshot(" in source and ".events(" in source
    assert "process.env.DBOUND_CERT_TOKEN" in source
    assert "task_completed:false" in source
    for prohibited in (".approve(", ".applyPatch(", ".autoMerge(", "process.env.OPENAI_API_KEY"):
        assert prohibited not in source


def test_host_certificate_reports_disallowed_external_integration_as_untested():
    source = (ROOT / "scripts/certify_platform.py").read_text()
    for text in ("external_providers_tested\": False", "vscode_host_tested\": False",
                 "docker_tested\": False", "DBOUND_HOME", "TemporaryDirectory"):
        assert text in source


def test_certify_script_uses_no_assert_statements():
    """``python -O`` deletes assert statements, calls inside them included.

    Under -O the real certificate used to skip two ``dbound state check`` runs and
    still list the backup check as passed. Every check must raise explicitly.
    """
    tree = ast.parse((ROOT / "scripts" / "certify_platform.py").read_text(encoding="utf-8"))
    lines = sorted(node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert))
    assert not lines, f"assert statements at lines {lines} vanish under python -O"


def test_certify_check_helper_still_fails_under_python_O():
    """The explicit check helper must raise AssertionError even with -O (no asserts stripped)."""
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('c', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m._expect(True, 'fine')\n"
        "try:\n"
        "    m._expect(False, 'boom')\n"
        "except AssertionError as e:\n"
        "    print('raised', e)\n"
        "else:\n"
        "    print('SILENT PASS')\n")
    result = subprocess.run([sys.executable, "-O", "-c", code, str(ROOT / "scripts" / "certify_platform.py")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "raised boom"


def test_host_gate_relies_on_our_own_exit_marker_not_only_on_vs_code_log_text():
    """The real-host gate must not depend solely on a line VS Code prints itself.

    That line appears only under some environments, so the gate could not be
    reproduced locally. The runner now writes its own exit line, and both
    workflows require it and forbid any other exit code.
    """
    runner = (ROOT / "apps" / "vscode" / "test" / "host" / "runner.cjs").read_text(encoding="utf-8")
    assert "DBOUND_HOST_RUNNER_EXIT code=${exitCode}" in runner
    assert "process.exitCode = 1" in runner
    for name in ("ci.yml", "platform-certification.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert '--marker "DBOUND_HOST_RUNNER_EXIT code=0"' in text, name
        assert '--forbid "DBOUND_HOST_RUNNER_EXIT code=[^0]"' in text, name
        assert '--marker "DBOUND_HOST_SMOKE_OK commands=5"' in text, name
        assert '--forbid "Extension host test runner exit code: [1-9]"' in text, name


def test_the_exit_marker_pattern_accepts_zero_and_rejects_everything_else(tmp_path):
    spec = importlib.util.spec_from_file_location("require_executed_gate", ROOT / "scripts" / "require_executed.py")
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    forbid = ["DBOUND_HOST_RUNNER_EXIT code=[^0]"]
    marker = ["DBOUND_HOST_RUNNER_EXIT code=0"]
    log = tmp_path / "host.log"
    log.write_text("DBOUND_HOST_RUNNER_EXIT code=0\n", encoding="utf-8")
    assert guard.check_log(log, marker, forbid) == []
    for bad in ("1", "2", "undefined", "null"):
        log.write_text(f"DBOUND_HOST_RUNNER_EXIT code={bad}\n", encoding="utf-8")
        assert guard.check_log(log, marker, forbid), bad
    log.write_text("no exit line at all\n", encoding="utf-8")
    assert guard.check_log(log, marker, forbid)
