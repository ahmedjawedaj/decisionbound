#!/usr/bin/env python3
"""Fail CI on missing Markdown pages, broken static links and missing API docs."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SOURCE = ROOT / "site_docs"


def validate() -> None:
    """Validate generated docs without requiring a browser or external URL."""
    if not (SITE / "index.html").is_file():
        raise SystemExit("Site must be built before verifying")
    failures: list[str] = []
    pages = list(SITE.glob("*.html"))
    if len(pages) < 10:
        failures.append("Documentation site has fewer than ten pages")
    for page in pages:
        html = page.read_text(encoding="utf-8")
        for match in re.findall(r'''(?:href|src)=["']([^"']+)["']''', html):
            if match.startswith(("http:", "https:", "mailto:", "#", "data:")):
                continue
            path = (page.parent / match.split("#")[0]).resolve()
            if not path.is_relative_to(SITE.resolve()) or not path.exists():
                failures.append(f"{page.name}: broken local reference {match}")
    api = SOURCE / "api-reference.md"
    if not api.exists() or "`TransferPolicy`" not in api.read_text():
        failures.append("Generated API reference missing transfer-policy docstrings")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Validated {len(pages)} documentation pages and their local links")


if __name__ == "__main__":
    validate()
