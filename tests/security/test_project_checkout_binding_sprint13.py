"""A copied manifest cannot impersonate the original local checkout."""
from __future__ import annotations
import shutil

import pytest

from decisionbound.identity import init_project, load_project
from decisionbound.storage import SQLiteRepository


def test_copied_manifest_same_name_owner_is_refused(tmp_path):
    store = SQLiteRepository(tmp_path/'private'/'state.sqlite3')
    original = tmp_path/'original'; original.mkdir()
    registered = init_project(original,store,name='shared-name',owner='same-owner')
    assert load_project(original,store) == registered
    clone = tmp_path/'clone'; clone.mkdir()
    shutil.copytree(original/'.decisionbound',clone/'.decisionbound')
    # This would previously succeed because the copied manifest had identical
    # project ID, owner and name; now checkout location must match.
    with pytest.raises(PermissionError,match='different checkout'):
        load_project(clone,store)
    with pytest.raises(PermissionError,match='another registered checkout'):
        init_project(clone,store)
    assert load_project(original,store) == registered


def test_legacy_unbound_project_requires_explicit_enrollment(tmp_path):
    store = SQLiteRepository(tmp_path/'state.db')
    project = tmp_path/'project';project.mkdir()
    registered=init_project(project,store)
    # Simulate a user database created before checkout binding existed.
    with store._connection() as db:
        db.execute('DELETE FROM settings WHERE key=?',('project_root:'+registered.project_id,))
    with pytest.raises(PermissionError,match='not enrolled'):
        load_project(project,store)
    assert init_project(project,store) == registered
    assert load_project(project,store) == registered


def test_concurrent_binding_is_first_writer_wins(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = SQLiteRepository(tmp_path/'state.db')
    first = tmp_path/'first';first.mkdir()
    second = tmp_path/'second';second.mkdir()
    registered=init_project(first,store)
    # Existing state cannot be rebound to a different root by a second caller.
    def bind(index):
        try:
            store.bind_project_root(registered.project_id, second if index % 2 else first)
            return 'original'
        except PermissionError:
            return 'refused'
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(bind,range(16)))
    assert 'original' in results and 'refused' in results
    assert load_project(first,store) == registered


def test_symlinked_manifest_parent_refused(tmp_path):
    store = SQLiteRepository(tmp_path/'state.db')
    real=tmp_path/'real';real.mkdir()
    init_project(real,store)
    alias=tmp_path/'alias';alias.mkdir()
    try:
        (alias/'.decisionbound').symlink_to(real/'.decisionbound',target_is_directory=True)
    except (OSError,NotImplementedError):
        pytest.skip('Symlink privilege unavailable')
    with pytest.raises(FileNotFoundError,match='symlinked'):
        load_project(alias,store)


def test_large_and_mutated_manifest_rejected(tmp_path):
    import yaml
    store=SQLiteRepository(tmp_path/'state.db')
    root=tmp_path/'root';root.mkdir()
    init_project(root,store,technologies=('python',))
    manifest=root/'.decisionbound'/'project.yaml'
    document=yaml.safe_load(manifest.read_text())
    document['technologies']=['javascript']
    manifest.write_text(yaml.safe_dump(document))
    with pytest.raises(PermissionError,match='conflicts'):
        load_project(root,store)
    manifest.write_text('x'*9000)
    with pytest.raises(ValueError,match='size limit'):
        load_project(root,store)


def test_manifest_with_forged_id_fails_schema_before_lookup(tmp_path):
    import yaml
    store=SQLiteRepository(tmp_path/'state.db')
    root=tmp_path/'root';root.mkdir()
    init_project(root,store)
    manifest=root/'.decisionbound'/'project.yaml'
    document=yaml.safe_load(manifest.read_text())
    document['project_id']='../../foreign'
    manifest.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError,match='Invalid versioned'):
        load_project(root,store)
