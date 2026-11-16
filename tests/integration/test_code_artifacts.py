"""Patch promotion requires human review and stable provenance."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from decisionbound.code_artifacts import export_coding_patch, ArtifactVerificationError
from tests.integration.test_code_workflow import PATCH, project, execute  # noqa: F401


def test_unverified_patch_requires_acknowledgment(project, tmp_path):
    receipt = execute(project)
    with pytest.raises(PermissionError):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id=receipt.run_id, destination=tmp_path/'out.patch')
    exported = export_coding_patch(repository=project[2], state_root=project[1],
                                   run_id=receipt.run_id, destination=tmp_path/'out.patch',
                                   acknowledge_unverified=True)
    assert exported['requires_review'] and not exported['task_completed']
    assert (tmp_path/'out.patch').read_text() == PATCH
    assert not (project[0]/'new.py').exists()
    with pytest.raises(FileExistsError):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id=receipt.run_id, destination=tmp_path/'out.patch',
                            acknowledge_unverified=True)


def test_tampered_patch_artifact_is_refused(project, tmp_path):
    receipt = execute(project)
    Path(receipt.patch_artifact).write_text('tampered')
    with pytest.raises(ArtifactVerificationError, match='no longer match'):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id=receipt.run_id, destination=tmp_path/'out.patch',
                            acknowledge_unverified=True)


def test_incomplete_or_unknown_run_cannot_export(project, tmp_path):
    with pytest.raises(ArtifactVerificationError):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id='code_'+'0'*32, destination=tmp_path/'x.patch',
                            acknowledge_unverified=True)
    with pytest.raises(ValueError):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id='../oops', destination=tmp_path/'x.patch',
                            acknowledge_unverified=True)


def test_symlinked_export_target_refused(project, tmp_path):
    receipt = execute(project)
    p = tmp_path/'out.patch'
    p.symlink_to(tmp_path/'somewhere')
    with pytest.raises(FileExistsError):
        export_coding_patch(repository=project[2], state_root=project[1],
                            run_id=receipt.run_id, destination=p, acknowledge_unverified=True)
