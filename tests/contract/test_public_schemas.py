"""Public schema contracts; independent of internal planning documents."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def test_public_schemas_are_strict_and_versioned():
    files = list((ROOT / 'schemas').glob('*.json'))
    assert len(files) >= 8
    for path in files:
        schema = json.loads(path.read_text())
        assert schema['additionalProperties'] is False
        assert 'schema_version' in schema['required']
        assert schema['properties']['schema_version']['const'] == (1 if path.name == 'workflow.v1.json' else '1.0')

def test_project_identity_excludes_credentials():
    schema = json.loads((ROOT / 'schemas/project-identity.v1.json').read_text())
    assert set(schema['properties']) == {'schema_version', 'project_id', 'name'}

def test_io_envelope_uses_bounded_references():
    schema = json.loads((ROOT / 'schemas/io-envelope.v1.json').read_text())
    assert {'media_type', 'size_bytes', 'sha256', 'visibility', 'storage_ref'} <= set(schema['required'])
    assert 'raw_contents' not in schema['properties']
