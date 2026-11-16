"""The package version must match the version declared for the distribution."""
from __future__ import annotations

from pathlib import Path
import tomllib

import decisionbound


def test_package_version_matches_pyproject():
    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert decisionbound.__version__ == declared


def test_citation_file_version_matches_pyproject():
    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lines = (root / "CITATION.cff").read_text(encoding="utf-8").splitlines()
    cited = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("version:"))
    assert cited == declared
