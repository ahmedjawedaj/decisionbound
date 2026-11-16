"""Adversarial ZIP checks for the independent release verifier."""
from __future__ import annotations
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    spec = importlib.util.spec_from_file_location('dbound_release_verify', ROOT/'scripts'/'verify_release.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def make_zip(path, names):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in names:
            archive.writestr(name, value)


@pytest.mark.parametrize('unsafe', [
    '../passwd', '/root/.ssh/id_rsa', 'docs/../../escape', 'C:/Windows/system.ini',
    'docs\\../../../escape', 'a//b', './entry',
])
def test_unsafe_archive_paths_rejected(tmp_path, unsafe):
    verifier = module()
    z = tmp_path/'invalid.zip'
    make_zip(z, [('index.html', 'fine'), ('assets/site.css', 'fine'),
                 ('assets/site.js', 'fine'), (unsafe, 'unsafe')])
    with pytest.raises(verifier.ArtifactVerificationError):
        verifier.verify_pages_archive(z)


def test_case_insensitive_duplicates_rejected(tmp_path):
    verifier = module()
    z = tmp_path/'duplicates.zip'
    make_zip(z, [('index.html', 'fine'), ('assets/site.css', 'fine'),
                 ('assets/site.js', 'fine'), ('ASSETS/SITE.CSS', 'evil')])
    with pytest.raises(verifier.ArtifactVerificationError, match='Duplicate'):
        verifier.verify_pages_archive(z)


def test_source_manifest_mismatch_refused(tmp_path):
    verifier = module()
    z = tmp_path/'bad-manifest.zip'
    prefix = 'decisionbound-v0.13.0a1/'
    manifest = {'format':'decisionbound-source-manifest-v1', 'version':'0.13.0a1',
                'source_count':1, 'files':{'README.md':'0'*64}}
    make_zip(z, [(prefix+'RELEASE_MANIFEST.json',json.dumps(manifest)),
                 (prefix+'README.md','tampered')])
    with pytest.raises(verifier.ArtifactVerificationError):
        verifier.verify_source_archive(z)


def test_archive_symlink_refused(tmp_path):
    import stat
    verifier = module()
    z = tmp_path/'symlink.zip'
    with zipfile.ZipFile(z, 'w') as out:
        for name in ('index.html', 'assets/site.css', 'assets/site.js'):
            out.writestr(name, 'fine')
        link = zipfile.ZipInfo('fake-link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        out.writestr(link, '/etc/passwd')
    with pytest.raises(verifier.ArtifactVerificationError, match='Symlink'):
        verifier.verify_pages_archive(z)


def test_malicious_pages_suffix_is_rejected(tmp_path):
    verifier = module()
    z = tmp_path/'pages.zip'
    make_zip(z, [('index.html', 'ok'),('assets/site.css','ok'),
                 ('assets/site.js','ok'),('private.key','x')])
    with pytest.raises(verifier.ArtifactVerificationError):
        verifier.verify_pages_archive(z)
