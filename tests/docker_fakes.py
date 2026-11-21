"""Stand-ins for the container CLI, so tests can check the host's handling without a daemon."""
from __future__ import annotations

import hashlib

from decisionbound.code_sandbox import _CliResult


def cli_result(returncode: int | None = 0, stdout: bytes = b'', stderr: bytes = b'', **flags) -> _CliResult:
    """What a finished CLI run looks like for the given output."""
    return _CliResult(returncode, hashlib.sha256(stdout).hexdigest(), hashlib.sha256(stderr).hexdigest(),
                      len(stdout) + len(stderr), **flags)
