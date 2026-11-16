"""Artifact inclusion/exclusion contract for a repeatable reviewed release."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_tool_has_safe_source_filter_and_stable_zip_metadata():
    source = (ROOT / "scripts" / "build_release.py").read_text()
    for term in (".decisionbound", ".env", ".git", "node_modules", ".pytest_cache",
                 "ZipInfo", "external_attr", "writestr", "sha256", "RELEASE_MANIFEST.json",
                 "testzip()", "dist/decisionbound-"):
        assert term in source
    assert "urlopen" not in source and "subprocess.run" not in source
