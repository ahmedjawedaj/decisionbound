"""Transactional, idempotency-keyed local journal for repair sessions.

The SQLite adapter serializes reservations and never retries an in-flight
attempt automatically. A restart can reconstruct a fully recorded attempt,
but if a write/test might still be running it fails closed for operator
reconciliation. This is *single-user local* durability, not distributed leases.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import SQLiteRepository

_KEY = re.compile(r'^[A-Za-z0-9_.:-]{8,96}$')


class RepairConflict(RuntimeError):
    """The session is already running, mismatches its key, or needs review."""


@dataclass(frozen=True)
class AttemptEntry:
    """Public, source-free checkpoint for one reserved coding attempt."""

    number: int
    run_id: str
    status: str
    summary: dict[str, Any]


@dataclass(frozen=True)
class RepairState:
    """Versioned status of a bounded repair session (not proof of completion)."""

    session_id: str
    status: str
    request_sha256: str
    contract_sha256: str
    max_attempts: int
    cost_usd: float
    feedback: dict[str, Any]
    attempts: tuple[AttemptEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize safe journal metadata for CLI, UI and integration clients."""
        return {'schema_version': '1.0', 'session_id': self.session_id, 'status': self.status,
                'request_sha256': self.request_sha256, 'contract_sha256': self.contract_sha256,
                'max_attempts': self.max_attempts, 'cost_usd': self.cost_usd,
                'feedback': self.feedback,
                'attempts': [{'number': x.number, 'run_id': x.run_id, 'status': x.status,
                              'summary': x.summary} for x in self.attempts]}


class SQLiteRepairJournal:
    """Single-user implementation of crash-safe repair attempt reservations.

    Requires a :class:`SQLiteRepository` initialized with schema version 2.
    Status transitions use `BEGIN IMMEDIATE` so duplicate callers cannot
    reserve two writes for one idempotency key. A parallel daemon should use
    a different backend with authenticated durable leases.
    """

    def __init__(self, repository: SQLiteRepository, state_root: Path):
        self.repository = repository
        self.state_root = state_root.expanduser().resolve(strict=True)

    def open(self, *, idempotency_key: str, request_sha256: str, project_id: str,
             contract_sha256: str, max_attempts: int) -> tuple[RepairState, bool]:
        """Create a session or retrieve the same immutable request by key."""
        if not _KEY.fullmatch(idempotency_key) or not 1 <= max_attempts <= 5:
            raise ValueError('Invalid idempotency key or attempt ceiling')
        if any(not re.fullmatch(r'[0-9a-f]{64}', s) for s in (request_sha256, contract_sha256)):
            raise ValueError('Invalid request or contract fingerprint')
        session_id = 'repair_' + uuid.uuid4().hex
        with self.repository._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM repair_sessions WHERE idempotency_key=?', (idempotency_key,)).fetchone()
            fresh = row is None
            if fresh:
                db.execute('''INSERT INTO repair_sessions
                    (id,idempotency_key,request_sha256,project_id,contract_sha256,max_attempts,status)
                    VALUES (?,?,?,?,?,?,?)''',
                    (session_id, idempotency_key, request_sha256, project_id,
                     contract_sha256, max_attempts, 'running'))
            elif (row['request_sha256'] != request_sha256 or row['project_id'] != project_id or
                  row['contract_sha256'] != contract_sha256 or row['max_attempts'] != max_attempts):
                raise RepairConflict('Idempotency key already belongs to another request')
            else:
                session_id = row['id']
        return self.get(session_id), fresh

    def get(self, session_id: str) -> RepairState:
        """Load checkpoints without returning raw prompts, source, or test output."""
        if not re.fullmatch(r'repair_[0-9a-f]{32}', session_id):
            raise ValueError('Invalid repair session ID')
        with self.repository._connection() as db:
            row = db.execute('SELECT * FROM repair_sessions WHERE id=?', (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            attempts = db.execute('SELECT * FROM repair_attempts WHERE session_id=? ORDER BY number',
                                  (session_id,)).fetchall()
        return RepairState(session_id, row['status'], row['request_sha256'], row['contract_sha256'],
                           row['max_attempts'], row['cost_usd'], json.loads(row['last_feedback']),
                           tuple(AttemptEntry(a['number'], a['run_id'], a['status'],
                                              json.loads(a['summary'])) for a in attempts))

    def reserve(self, session_id: str, *, cost_reservation_usd: float = 0.0) -> AttemptEntry:
        """Reserve exactly one new run ID before any external model/Git action."""
        if (not isinstance(cost_reservation_usd, (int, float)) or
                not math.isfinite(cost_reservation_usd) or cost_reservation_usd < 0):
            raise ValueError('Invalid paid-attempt budget reservation')
        with self.repository._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,max_attempts FROM repair_sessions WHERE id=?',
                             (session_id,)).fetchone()
            if row is None or row['status'] != 'running':
                raise RepairConflict('Session is not eligible for another attempt')
            count = db.execute('SELECT COUNT(*) FROM repair_attempts WHERE session_id=?',
                               (session_id,)).fetchone()[0]
            pending = db.execute('SELECT COUNT(*) FROM repair_attempts WHERE session_id=? AND status=?',
                                 (session_id, 'in_flight')).fetchone()[0]
            if pending or count >= row['max_attempts']:
                raise RepairConflict('Active or exhausted attempt; reconcile before retry')
            run_id = 'code_' + uuid.uuid4().hex
            db.execute('''INSERT INTO repair_attempts(session_id,number,run_id,status,reserved_cost_usd)
                        VALUES (?,?,?,?,?)''',
                       (session_id, count + 1, run_id, 'in_flight', float(cost_reservation_usd)))
            if cost_reservation_usd:
                db.execute('UPDATE repair_sessions SET cost_usd=cost_usd+? WHERE id=?',
                           (float(cost_reservation_usd), session_id))
        return AttemptEntry(count + 1, run_id, 'in_flight', {})

    def finish_attempt(self, session_id: str, run_id: str, *, status: str,
                       summary: dict[str, Any], cost_delta: float = 0.0,
                       feedback: dict[str, Any] | None = None) -> RepairState:
        """Commit a terminal attempt and its cost in one SQLite transaction."""
        if status not in {'passed', 'failed_test', 'failed_contract', 'mock_passed', 'blocked'}:
            raise ValueError('Invalid attempt result')
        if not isinstance(cost_delta, (float, int)) or not math.isfinite(cost_delta) or cost_delta < 0:
            raise ValueError('Invalid cost accounting')
        if len(json.dumps(summary)) > 2048 or len(json.dumps(feedback or {})) > 2048:
            raise ValueError('Journal metadata must remain small')
        with self.repository._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            reservation = db.execute('''SELECT reserved_cost_usd FROM repair_attempts
                WHERE session_id=? AND run_id=? AND status='in_flight' ''',
                (session_id, run_id)).fetchone()
            if reservation is None:
                raise RepairConflict('Attempt was already finalized or belongs to another session')
            n = db.execute('''UPDATE repair_attempts SET status=?,summary=?
                WHERE session_id=? AND run_id=? AND status='in_flight' ''',
                (status, json.dumps(summary, sort_keys=True), session_id, run_id)).rowcount
            if n != 1:
                raise RepairConflict('Attempt was already finalized or belongs to another session')
            db.execute('UPDATE repair_sessions SET cost_usd=cost_usd+?,last_feedback=? WHERE id=?',
                       (float(cost_delta - reservation['reserved_cost_usd']), json.dumps(feedback or {}, sort_keys=True), session_id))
        return self.get(session_id)

    def close(self, session_id: str, status: str) -> RepairState:
        """Mark a session terminal, refusing closure with unaccounted writes."""
        if status not in {'verified', 'exhausted', 'blocked', 'needs_real_tests'}:
            raise ValueError('Invalid session status')
        with self.repository._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('''SELECT COUNT(*) FROM repair_attempts
                WHERE session_id=? AND status='in_flight' ''', (session_id,)).fetchone()[0]
            if active:
                raise RepairConflict('Cannot close an in-flight attempt')
            current = db.execute('SELECT status FROM repair_sessions WHERE id=?', (session_id,)).fetchone()
            if current is None or current['status'] != 'running':
                raise RepairConflict('Session is not running')
            db.execute('UPDATE repair_sessions SET status=? WHERE id=?', (status, session_id))
        return self.get(session_id)

    def reconcile(self, session_id: str) -> RepairState:
        """Reconstruct only a *completed and intact* attempt after process loss.

        A started but unfinished coding run is never automatically replayed. Its
        worktree may contain unreviewed changes or an active sandbox process.
        The operator must inspect and clean it before starting another session.
        """
        state = self.get(session_id)
        active = [a for a in state.attempts if a.status == 'in_flight']
        if not active:
            return state
        if len(active) != 1:
            raise RepairConflict('Multiple ambiguous in-flight attempts')
        run = active[0]
        events = self.repository.events(run.run_id)
        completed = [e['payload'] for e in events if e['event'] == 'code_finished']
        patches = [e['payload'] for e in events if e['event'] == 'code_patch_applied']
        cleaned = [e['payload'] for e in events if e['event'] == 'code_workspace_cleaned']
        if len(completed) != 1 or len(patches) != 1 or len(cleaned) != 1 or cleaned[0].get('ok') is not True:
            raise RepairConflict('In-flight operation is ambiguous; manual reconciliation required')
        path = self.state_root / 'code-artifacts' / (run.run_id + '.patch')
        if path.is_symlink() or not path.is_file():
            raise RepairConflict('Completed attempt has no trustworthy patch artifact')
        data = path.read_bytes()
        if (hashlib.sha256(data).hexdigest() != patches[0].get('sha256') or
                len(data) != patches[0].get('size_bytes')):
            raise RepairConflict('Completed attempt patch checksum mismatch')
        verification = [e['payload'] for e in events if e['event'] == 'code_verification']
        if (len(verification) != 1 or
                bool(verification[0].get('contract_verified')) != bool(completed[0].get('contract_verified'))):
            raise RepairConflict('Completed run verification evidence is inconsistent')
        receipts = [e['payload'] for e in events if e['event'] == 'code_test_receipt']
        integrity = [e['payload'] for e in events if e['event'] == 'code_post_test_integrity']
        if len(integrity) != 1 or not receipts:
            raise RepairConflict('Completed attempt lacks independent test metadata')
        if not integrity[0].get('unchanged'):
            result = 'blocked'
        elif (completed[0].get('contract_verified') is True and
              all(r.get('kind') == 'docker' and r.get('exit_code') == 0 and
                  not r.get('timed_out') for r in receipts)):
            result = 'passed'
        elif any(r.get('exit_code') not in (0, None) or r.get('timed_out') for r in receipts):
            result = 'failed_test'
        elif any(r.get('kind') == 'mock' for r in receipts):
            result = 'mock_passed' if not any(
                e['payload'].get('passed') is False for e in events if e['event'] == 'code_contract_check') else 'failed_contract'
        elif any(e['payload'].get('passed') is False for e in events if e['event'] == 'code_contract_check'):
            result = 'failed_contract'
        else:
            result = 'blocked'
        summary = {'recovered_from_host_receipts': True, 'test_exit_codes': [r.get('exit_code') for r in receipts],
                   'contract_verified': bool(completed[0].get('contract_verified')),
                   'last_run_id': run.run_id, 'patch_sha256': patches[0].get('sha256', '')}
        # Crash-window model spending is unknown. Keep its pre-call reservation
        # charged instead of optimistically releasing it or making another call.
        with self.repository._connection() as db:
            reserved = db.execute('SELECT reserved_cost_usd FROM repair_attempts WHERE run_id=?',
                                  (run.run_id,)).fetchone()[0]
        return self.finish_attempt(session_id, run.run_id, status=result, summary=summary,
                                   cost_delta=reserved, feedback={'failure_kind': result,
                                             'failed_test_indices': [i for i, r in enumerate(receipts)
                                                                     if r.get('exit_code') != 0 or r.get('timed_out')],
                                             'previous_attempt_sha256': patches[0].get('sha256', '')}
                                   if result in {'failed_test', 'failed_contract'} else {})
