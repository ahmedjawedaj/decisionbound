"""Host-certification harness checks independent of installed vendor binaries."""
from __future__ import annotations
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
