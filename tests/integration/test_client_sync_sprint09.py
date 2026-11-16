"""Sprint 09 integration/contract tests for shared local client synchronization."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from decisionbound.client_sync import LocalClientSync, LocalSyncError
from decisionbound.cli import app
from decisionbound.identity import init_project
from decisionbound.settings import repository
from decisionbound.web import create_app, local_token
from decisionbound.workflows import WorkflowJournal, WorkflowSpec, WorkflowTask, WorkflowSupervisor


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setenv("DBOUND_HOME", str(tmp_path / "private"))
    store = repository()
    project_a = tmp_path / "repo_a"
    project_b = tmp_path / "repo_b"
    project_a.mkdir(); project_b.mkdir()
    a = init_project(project_a, store)
    b = init_project(project_b, store)
    journal = WorkflowJournal(store)
    spec = WorkflowSpec(tasks=(WorkflowTask("inspect", "prepare", "Inspect the repository"),))
    a_ids = [journal.create(project_path=project_a, spec=spec, idempotency_key=f"alpha_{i:08d}")
             for i in range(5)]
    b_id = journal.create(project_path=project_b, spec=spec, idempotency_key="other_12345678")
    return store, project_a, a, project_b, b, a_ids, b_id


def test_stable_project_scoped_session_pagination(shared):
    store, _, a, _, b, ids, foreign = shared
    svc = LocalClientSync(store)
    page1 = svc.list_sessions(a.project_id, limit=2)
    assert len(page1["sessions"]) == 2
    assert page1["next_before"] is not None
    page2 = svc.list_sessions(a.project_id, limit=2, before=page1["next_before"])
    page3 = svc.list_sessions(a.project_id, limit=2, before=page2["next_before"])
    all_ids = [x["session_id"] for page in (page1, page2, page3) for x in page["sessions"]]
    assert set(all_ids) == set(ids)
    assert len(set(all_ids)) == 5
    assert foreign not in all_ids
    assert page3["next_before"] is None
    # A newer event must not reorder the creation-based session pagination.
    WorkflowSupervisor(WorkflowJournal(store)).run(ids[0])
    again = svc.list_sessions(a.project_id, limit=2)
    assert [s["session_id"] for s in again["sessions"]] == [s["session_id"] for s in page1["sessions"]]
    assert svc.list_sessions(b.project_id)["sessions"][0]["session_id"] == foreign


def test_incremental_cursor_and_reconnect_without_duplicates(shared):
    store, _, a, _, b, ids, foreign = shared
    svc = LocalClientSync(store)
    page = svc.changes(a.project_id, limit=2)
    assert page["has_more"] is True
    observed = list(page["events"])
    cursor = page["next_after"]
    while True:
        chunk = svc.changes(a.project_id, after=cursor, limit=2)
        observed.extend(chunk["events"])
        cursor = chunk["next_after"]
        if not chunk["has_more"]:
            break
    assert len(observed) == len(ids)
    assert len({e["id"] for e in observed}) == len(ids)
    assert all(e["session_id"] in ids for e in observed)
    assert svc.changes(a.project_id, after=cursor)["events"] == []
    assert svc.changes(b.project_id)["events"][0]["session_id"] == foreign
    WorkflowSupervisor(WorkflowJournal(store)).run(ids[0])
    resumed = svc.changes(a.project_id, after=cursor)
    assert resumed["events"]
    assert all(e["id"] > cursor for e in resumed["events"])
    assert all(e["session_id"] == ids[0] for e in resumed["events"])
    own = svc.session_events(a.project_id, ids[0], limit=1)
    second = svc.session_events(a.project_id, ids[0], after=own["next_after"], limit=100)
    assert all(e["id"] > own["next_after"] for e in second["events"])


def test_strict_validation_and_foreign_scope(shared):
    store, _, a, _, b, ids, foreign = shared
    svc = LocalClientSync(store)
    with pytest.raises(LocalSyncError, match="Unknown session"):
        svc.snapshot(a.project_id, foreign)
    with pytest.raises(LocalSyncError, match="Unknown session"):
        svc.session_events(a.project_id, foreign)
    for invalid in (-1, 101, True, 1.5):
        with pytest.raises(LocalSyncError):
            svc.changes(a.project_id, limit=invalid)
    with pytest.raises(LocalSyncError):
        svc.changes(a.project_id, after=-1)
    with pytest.raises(LocalSyncError):
        svc.list_sessions(a.project_id, before=0)
    with pytest.raises(LocalSyncError):
        svc.list_sessions('prj_' + '0' * 32)
    with pytest.raises(LocalSyncError):
        svc.snapshot(a.project_id, '../escape')
    assert 'model_key' not in str(svc.capabilities()).lower()


def test_cli_web_same_sessions_cursor_and_auth(shared):
    store, root, a, _, b, ids, foreign = shared
    runner = CliRunner()
    cli = runner.invoke(app, ['client', 'sessions', '--path', str(root), '--limit', '2'])
    assert cli.exit_code == 0, cli.output
    client = TestClient(create_app())
    url = '/api/client/v1/sessions'
    assert client.get(url, params={'project_id': a.project_id}).status_code == 401
    headers = {'Authorization': 'Bearer ' + local_token()}
    response = client.get(url, params={'project_id': a.project_id, 'limit': 2}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == json.loads(cli.output)
    cap = client.get('/api/client/v1/capabilities', headers=headers)
    assert cap.json() == LocalClientSync.capabilities()
    assert 'token' not in str(cap.json()).lower()
    assert client.get(url, params={'project_id': a.project_id, 'limit': 101}, headers=headers).status_code == 422
    assert client.get(url, params={'project_id': a.project_id}, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.get(url, params={'project_id': a.project_id}, headers={**headers, 'Host': 'localhost.attacker.invalid'}).status_code == 403
    assert client.get(f'/api/client/v1/sessions/{foreign}', params={'project_id': a.project_id}, headers=headers).status_code == 404
    status = client.get(f'/api/client/v1/sessions/{ids[0]}', params={'project_id': a.project_id}, headers=headers)
    assert status.status_code == 200
    assert status.json() == WorkflowJournal(store).status(ids[0])
    changes = client.get('/api/client/v1/changes', params={'project_id': a.project_id, 'limit': 2}, headers=headers)
    assert changes.status_code == 200
    assert foreign not in str(changes.json())
    cli_changes = runner.invoke(app, ['client', 'changes', '--path', str(root), '--limit', '2'])
    assert cli_changes.exit_code == 0, cli_changes.output
    assert json.loads(cli_changes.output) == changes.json()
    cli_status = runner.invoke(app, ['client', 'inspect', ids[0], '--path', str(root)])
    assert cli_status.exit_code == 0
    assert json.loads(cli_status.output) == status.json()
    unauthorized = client.get('/api/client/v1/capabilities', headers={'Authorization': 'Bearer wrong'})
    assert unauthorized.status_code == 401


def test_concurrent_reader_and_writer_never_invents_completion(shared):
    store, _, a, _, _, ids, _ = shared
    svc = LocalClientSync(store)
    journal = WorkflowJournal(store)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(svc.list_sessions, a.project_id, limit=5) for _ in range(4)]
        futures.append(pool.submit(WorkflowSupervisor(journal).run, ids[0]))
        for future in futures:
            result = future.result()
            assert result['task_completed'] is False if 'task_completed' in result else all(
                item['task_completed'] is False for item in result['sessions'])
    events = svc.changes(a.project_id)
    assert all('lease_token' not in json.dumps(e) and 'secret' not in json.dumps(e)
               for e in events['events'])


def test_pairing_token_requires_reveal_and_never_enters_session_payload(shared):
    runner = CliRunner()
    normal = runner.invoke(app, ['client', 'pair'])
    assert normal.exit_code == 0
    from decisionbound.web import local_token
    assert local_token() not in normal.output
    explicit = runner.invoke(app, ['client', 'pair', '--show-token'])
    assert explicit.exit_code == 0
    assert explicit.output.strip() == local_token().strip()
