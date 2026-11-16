"""Offline, real Ed25519 checks; never ship or store a production signing key."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

cryptography = pytest.importorskip('cryptography')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from decisionbound.cli import app
from decisionbound.release_trust import (canonical_manifest, verify_signed_release,
                                         ReleaseTrustError)


@pytest.fixture
def signed(tmp_path):
    private = Ed25519PrivateKey.generate()  # ephemeral and never written to disk
    public = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    key = tmp_path/'trusted-publisher.pem'
    key.write_bytes(public)
    directory = tmp_path/'artifacts'
    directory.mkdir()
    artifact = directory/'DecisionBound-v0.13.0a1-Full-Source.zip'
    artifact.write_bytes(b'only a test artifact, not installable')
    data = {
      'format': 'decisionbound-signed-release-v1',
      'channel': 'alpha', 'version': '0.13.0a1',
      'issued_at': '2026-11-01T00:00:00Z',
      'expires_at': '2026-12-01T00:00:00Z',
      'artifacts': [{'name':artifact.name, 'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
                     'size_bytes':artifact.stat().st_size}],
    }
    manifest = tmp_path/'release.json'
    manifest.write_bytes(canonical_manifest(data))
    signature = tmp_path/'release.sig'
    signature.write_bytes(base64.b64encode(private.sign(manifest.read_bytes())))
    return (manifest, signature, key, directory, artifact, private, data)


def verify(signed, **options):
    manifest, sig, key, folder, *_ = signed
    return verify_signed_release(manifest, sig, key, folder,
        installed_version=options.pop('installed_version', '0.12.0a1'),
        now=options.pop('now', datetime(2026,11,14,tzinfo=timezone.utc)), **options)


def test_valid_signature_and_artifact_hash(signed):
    outcome=verify(signed)
    assert outcome['signature_verified'] is True
    assert outcome['artifact_count']==1
    assert outcome['automatic_installation_performed'] is False
    assert outcome['publisher_key_rotation_verified'] is False


def test_artifact_tampering_invalidates_verified_release(signed):
    signed[4].write_bytes(b'bad modifications')
    with pytest.raises(ReleaseTrustError):
        verify(signed)


def test_signer_cannot_be_substituted(signed,tmp_path):
    other=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo)
    signed[2].write_bytes(other)
    with pytest.raises(ReleaseTrustError,match='signature'):
        verify(signed)


def test_manifest_tampering_detected(signed):
    raw=signed[0].read_bytes().replace(b'"alpha"',b'"stable"')
    signed[0].write_bytes(raw)
    with pytest.raises(ReleaseTrustError,match='signature'):
        verify(signed)


def test_expired_future_and_anti_rollback(signed):
    with pytest.raises(ReleaseTrustError,match='expired'):
        verify(signed,now=datetime(2027,1,1,tzinfo=timezone.utc))
    with pytest.raises(ReleaseTrustError,match='not yet valid'):
        verify(signed,now=datetime(2025,1,1,tzinfo=timezone.utc))
    with pytest.raises(ReleaseTrustError,match='downgrade'):
        verify(signed,installed_version='0.14.0')
    with pytest.raises(ReleaseTrustError,match='downgrade'):
        verify(signed,minimum_accepted_version='0.15.0')


def test_signed_manifest_with_path_traversal_filename_rejected(signed):
    manifest,sig,key,folder,artifact,private,data=signed
    data['artifacts'][0]['name']='../../escape.zip'
    manifest.write_bytes(canonical_manifest(data))
    sig.write_bytes(base64.b64encode(private.sign(manifest.read_bytes())))
    with pytest.raises(ReleaseTrustError,match='filename'):
        verify(signed)


def test_duplicate_signed_json_keys_rejected(signed):
    signed[0].write_text('{"format":"a","format":"b"}\n')
    with pytest.raises(ReleaseTrustError,match='Duplicate'):
        verify(signed)


def test_symlinked_artifact_refused(signed,tmp_path):
    artifact=signed[4]
    original=tmp_path/'separate.zip'
    artifact.rename(original)
    try:
        artifact.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip('Symlink permissions unavailable')
    with pytest.raises(ReleaseTrustError,match='symlinked'):
        verify(signed)


def test_cli_verify_rejects_wrong_trust_and_accepts_correct_key(signed):
    manifest,sig,key,folder,*_ = signed
    args=['release','verify','--manifest',str(manifest),'--signature',str(sig),
          '--trusted-public-key',str(key),'--artifacts-dir',str(folder),
          '--installed-version','0.12.0a1']
    # The real wall clock may be outside the fixture's validity; use a fixture
    # with a generously current window by resigning when necessary.
    from datetime import datetime
    stamp=datetime.now(timezone.utc)
    _,_,_,_,_,private,data=signed
    from datetime import timedelta
    data['issued_at']=(stamp-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    data['expires_at']=(stamp+timedelta(days=60)).strftime('%Y-%m-%dT%H:%M:%SZ')
    manifest.write_bytes(canonical_manifest(data))
    sig.write_bytes(base64.b64encode(private.sign(manifest.read_bytes())))
    good=CliRunner().invoke(app,args)
    assert good.exit_code==0,(good.output,good.exception)
    assert json.loads(good.output)['signature_verified'] is True
    wrong=CliRunner().invoke(app,args[:-1]+['0.99.0'])
    assert wrong.exit_code!=0


def test_required_release_extra_and_safe_inputs_documented():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    assert 'cryptography>=43' in (root/'pyproject.toml').read_text()
    assert 'release_trust' in (root/'scripts/generate_api_docs.py').read_text()


def test_bad_base64_signature_rejected_gracefully(signed):
    signed[1].write_bytes(b'no%%%base64')
    with pytest.raises(ReleaseTrustError, match='signature'):
        verify(signed)


def test_reject_excessive_manifest_expiry_even_if_signature_valid(signed):
    manifest, sig, _, _, _, private, data = signed
    data['expires_at']='2030-11-01T00:00:00Z'
    manifest.write_bytes(canonical_manifest(data))
    sig.write_bytes(base64.b64encode(private.sign(manifest.read_bytes())))
    with pytest.raises(ReleaseTrustError, match='validity window'):
        verify(signed)
