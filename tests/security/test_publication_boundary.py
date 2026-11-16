"""Public distribution rejects private files and implicit directory approval."""
import importlib.util
import json
from pathlib import Path
import pytest

script = Path(__file__).resolve().parents[2] / 'scripts/check_publication.py'
spec = importlib.util.spec_from_file_location('publication_boundary', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest(root, paths):
    (root / 'PUBLIC_CONTENT.json').write_text(json.dumps({'format': 'decisionbound-public-content-v1', 'files': ['PUBLIC_CONTENT.json', *paths]}))


def test_unlisted_document_is_rejected(tmp_path):
    manifest(tmp_path, [])
    (tmp_path / 'operational-notes.md').write_text('private fixture')
    with pytest.raises(ValueError, match='Unreviewed public files'):
        module.audit(tmp_path)


def test_internal_report_cannot_be_allowlisted(tmp_path):
    name = 'SPRINT99_REPORT.md'
    (tmp_path / name).write_text('private fixture')
    manifest(tmp_path, [name])
    with pytest.raises(ValueError, match='Private, duplicate or unsafe'):
        module.audit(tmp_path)


def test_reviewed_source_and_generated_build_are_separate(tmp_path):
    (tmp_path / 'README.md').write_text('public fixture')
    (tmp_path / 'dist').mkdir()
    (tmp_path / 'dist/example.whl').write_bytes(b'generated fixture')
    manifest(tmp_path, ['README.md'])
    assert module.audit(tmp_path) == {'README.md', 'PUBLIC_CONTENT.json'}
