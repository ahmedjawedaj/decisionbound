"""Review approvals must propagate atomically through the shared client feed."""
from __future__ import annotations

from fastapi.testclient import TestClient

from decisionbound.client_sync import LocalClientSync
from decisionbound.identity import init_project
from decisionbound.review_gate import ReviewGate
from decisionbound.web import create_app, local_token
from tests.integration.test_review_gate_sprint08 import completed


def test_review_mutations_are_in_the_project_change_feed(completed, tmp_path):
    store, state, root, wf = completed
    project_id = ReviewGate(store, state).snapshot(wf)['project_id']
    svc = LocalClientSync(store)
    before = svc.changes(project_id)
    cursor = before['next_after']
    gate = ReviewGate(store, state)
    fp = gate.snapshot(wf)['fingerprint']
    assert svc.review_state(project_id, wf)['approval']['decision'] == 'pending'
    gate.decide(wf, expected_fingerprint=fp, expected_revision=0,
                action='approve_export', acknowledge_unverified=True)
    after = svc.changes(project_id, after=cursor)
    assert len(after['events']) == 1
    event = after['events'][0]
    assert event['event'] == 'review_decision'
    assert event['metadata'] == {'decision': 'approve_export', 'revision': 1}
    assert 'fingerprint' not in str(event)
    assert svc.review_state(project_id, wf)['approval']['revision'] == 1
    gate.decide(wf, expected_fingerprint=fp, expected_revision=1, action='revoke')
    again = svc.changes(project_id, after=after['next_after'])
    assert again['events'][0]['metadata']['decision'] == 'revoke'
    assert svc.review_state(project_id, wf)['approval']['decision'] == 'revoke'
    assert svc.review_state(project_id, wf)['task_completed'] is False


def test_review_api_is_project_scoped_read_only_and_never_returns_diff(completed, tmp_path):
    store, state, root, wf = completed
    project = ReviewGate(store, state).snapshot(wf)['project_id']
    other = tmp_path / 'other'
    other.mkdir()
    foreign_id = init_project(other, store).project_id
    client = TestClient(create_app())
    url = '/api/client/v1/sessions/' + wf + '/review'
    headers = {'Authorization': 'Bearer ' + local_token()}
    assert client.get(url, params={'project_id': project}).status_code == 401
    assert client.get(url, params={'project_id': foreign_id}, headers=headers).status_code == 404
    result = client.get(url, params={'project_id': project}, headers=headers)
    assert result.status_code == 200
    body = result.json()
    assert body['available'] is True
    assert body['approval']['decision'] == 'pending'
    assert 'diff' not in str(body)
    assert 'VALUE = 2' not in str(body)
    assert 'token' not in str(body)
    assert client.post(url, params={'project_id': project}, headers=headers).status_code == 405


def test_tampered_approval_remains_historical_not_current(completed):
    store, state, _, wf = completed
    gate = ReviewGate(store, state)
    evidence = gate.snapshot(wf)
    project = evidence['project_id']
    gate.decide(wf, expected_fingerprint=evidence['fingerprint'], expected_revision=0,
                action='approve_export', acknowledge_unverified=True)
    artifact = state / 'code-artifacts' / (evidence['run_id'] + '.patch')
    artifact.write_bytes(artifact.read_bytes() + b'\n# tampered\n')
    state = LocalClientSync(store).review_state(project, wf)
    assert state['approval']['decision'] == 'approve_export'
    assert state['available'] is False
    assert state['evidence'] is None
    assert state['task_completed'] is False
