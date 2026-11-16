#!/usr/bin/env python3
"""Install exactly the wheel matching pyproject version on any supported OS.

Avoids shell-dependent wildcard expansion and hard-coded versions in GitHub CI.
This script is intentionally standalone and makes no network calls itself;
`--no-deps` also forces pip's offline no-index mode.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def wheel_path(root: Path = ROOT) -> Path:
    """Return one exact wheel or fail instead of using an obsolete artifact."""
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    path = root / "dist" / f"decisionbound-{version}-py3-none-any.whl"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"Expected wheel is missing or symlinked: {path}")
    return path


def main() -> None:
    """Run pip against one version-verified local wheel, without a host shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-deps", action="store_true", help="Use preinstalled dependencies, no index")
    args = parser.parse_args()
    wheel = wheel_path()
    command = [sys.executable, "-m", "pip", "install"]
    if args.no_deps:
        command.extend(("--no-index", "--no-deps"))
    command.append(str(wheel))
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
