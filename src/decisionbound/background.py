"""Crash-tolerant, single-device background coordination for local DAG workflows.

A running process is deliberately NOT a durable authority: SQLite owns the
leadership lease, workflow claims and receipts. An external operating-system
service manager should restart this process after power loss. This module never
starts model calls, shells or coding workers implicitly; injected workers must
be independently authorized and sandboxed by their operators.

This is **not** a distributed consensus implementation, a task-killing sandbox,
or a multi-tenant cloud service. It is a bounded local service primitive.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from typing import Mapping

from .storage import SQLiteRepository
from .workflows import (WorkflowConflict, WorkflowJournal, WorkflowSupervisor,
                        WorkflowWorker, PreparationWorker, IndependentTaskVerifier)


class CoordinatorConflict(RuntimeError):
    """Coordinator ownership is stale, busy, or cannot be safely renewed."""


class ResourceBudgetExceeded(RuntimeError):
    """The local process exceeded an operator-defined resource admission cap."""


def resident_memory_mib() -> float:
    """Measure current process RSS without third-party dependencies.

    On Linux, use the process' resident page count. On Windows, use the current
    working set from ``GetProcessMemoryInfo``. On macOS or other POSIX
    platforms, fall back to ru_maxrss (peak rather than current RSS). Unsupported
    platforms fail closed when a resource cap is requested, never claim 0 MiB.
    """
    if os.path.isfile('/proc/self/statm'):
        with open('/proc/self/statm', encoding='ascii') as stream:
            pages = int(stream.read().split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE') / 1024**2
    if os.name == 'posix':
        import resource
        import sys
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024**2 if sys.platform == 'darwin' else 1024)
    if os.name == 'nt':
        working_set = _windows_working_set_bytes()
        if working_set is not None:
            return working_set / 1024**2
    raise RuntimeError('Current process RSS measurement unavailable on this host')


def _windows_working_set_bytes() -> int | None:
    """Return the current process working set on Windows, or None on failure."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]

    try:
        kernel32 = ctypes.WinDLL('kernel32')
        psapi = ctypes.WinDLL('psapi')
    except OSError:
        return None
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters),
                                           wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize)


@dataclass(frozen=True)
class CoordinatorSettings:
    """Small bounded service configuration with explicit local resource caps.

    ``max_seconds`` limits the coordinator's scheduling lifetime, not the
    execution time of arbitrary worker processes. Worker plugins MUST enforce
    their own hard process/network deadlines. ``max_rss_mib=None`` disables the
    RSS guard explicitly; otherwise an unavailable measurement fails closed.
    """
    poll_seconds: float = 2.0
    leader_lease_seconds: int = 30
    task_lease_seconds: int = 30
    max_rss_mib: float | None = 512
    max_workflows_per_cycle: int = 8
    max_dispatch_per_workflow: int = 2
    max_wall_seconds_per_workflow: int = 30

    def __post_init__(self) -> None:
        if (not 0.05 <= self.poll_seconds <= 60 or
            type(self.leader_lease_seconds) is not int or
            not 10 <= self.leader_lease_seconds <= 300 or
            type(self.task_lease_seconds) is not int or
            not 5 <= self.task_lease_seconds <= 3600 or
            type(self.max_workflows_per_cycle) is not int or
            not 1 <= self.max_workflows_per_cycle <= 100 or
            type(self.max_dispatch_per_workflow) is not int or
            not 1 <= self.max_dispatch_per_workflow <= 200 or
            type(self.max_wall_seconds_per_workflow) is not int or
            not 5 <= self.max_wall_seconds_per_workflow <= 3600 or
            self.max_rss_mib is not None and
            (isinstance(self.max_rss_mib, bool) or not 32 <= self.max_rss_mib <= 1048576)):
            raise ValueError('Invalid coordinator resource or scheduling limits')


@dataclass(frozen=True)
class Leadership:
    """Private fencing credentials; never transmit to untrusted workers."""
    generation: int
    owner_token: str


class LocalCoordinatorLeader:
    """Exclusive SQLite-backed leadership lease for one local background loop.

    All ownership transitions use ``BEGIN IMMEDIATE`` and generation/token CAS.
    Expiry permits leadership takeover only after the old lease dies; a paused
    old process cannot renew or release the new owner's lease. Actual worker
    task ownership is separately fenced by :class:`WorkflowJournal`.
    """

    def __init__(self, store: SQLiteRepository):
        self.store = store
        with store._connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS local_coordinator_lease (
                name TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                owner_token TEXT, expires_at REAL NOT NULL,
                last_heartbeat REAL NOT NULL, pid INTEGER NOT NULL
            )''')

    def acquire(self, *, ttl_seconds: int = 30, now: float | None = None) -> Leadership:
        """Obtain unique leadership or fail without replacing an active owner."""
        if type(ttl_seconds) is not int or not 10 <= ttl_seconds <= 300:
            raise ValueError('Invalid leadership TTL')
        t = time.time() if now is None else now
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute("SELECT * FROM local_coordinator_lease WHERE name='default'").fetchone()
            if old and old['owner_token'] is not None and old['expires_at'] > t:
                raise CoordinatorConflict('Another local coordinator already owns this state database')
            generation = (old['generation'] if old else 0) + 1
            token = secrets.token_urlsafe(32)
            db.execute('''INSERT INTO local_coordinator_lease
                (name,generation,owner_token,expires_at,last_heartbeat,pid)
                VALUES ('default',?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET generation=excluded.generation,
                owner_token=excluded.owner_token,expires_at=excluded.expires_at,
                last_heartbeat=excluded.last_heartbeat,pid=excluded.pid''',
                (generation, token, t + ttl_seconds, t, os.getpid()))
            return Leadership(generation, token)

    def renew(self, leadership: Leadership, *, ttl_seconds: int = 30,
              now: float | None = None) -> None:
        """Extend only the live generation; reject paused or replaced owners."""
        if type(ttl_seconds) is not int or not 10 <= ttl_seconds <= 300:
            raise ValueError('Invalid leadership TTL')
        t = time.time() if now is None else now
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('''UPDATE local_coordinator_lease SET expires_at=?,last_heartbeat=?
                WHERE name='default' AND generation=? AND owner_token=? AND expires_at>?''',
                (t + ttl_seconds, t, leadership.generation, leadership.owner_token, t)).rowcount
            if count != 1:
                raise CoordinatorConflict('Lost or expired coordinator leadership')

    def release(self, leadership: Leadership) -> bool:
        """Clear only this owner's lease; never clear a replacement's lease."""
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('''UPDATE local_coordinator_lease SET owner_token=NULL,expires_at=0
                WHERE name='default' AND generation=? AND owner_token=?''',
                (leadership.generation, leadership.owner_token)).rowcount
        return count == 1

    def status(self, *, now: float | None = None) -> dict:
        """Expose read-only coordinator health without revealing credentials."""
        t = time.time() if now is None else now
        with self.store._connection() as db:
            row = db.execute('SELECT * FROM local_coordinator_lease WHERE name=?', ('default',)).fetchone()
        return {'running': bool(row and row['owner_token'] is not None and row['expires_at'] > t),
                'generation': row['generation'] if row else 0,
                'pid': row['pid'] if row and row['owner_token'] else None,
                'last_heartbeat': row['last_heartbeat'] if row else None}


class BackgroundCoordinator:
    """Restartable local service that schedules bounded, explicitly registered workers.

    ``workers`` is an allowlisted registry; default policy enables only offline
    preparation. Operator-approved coding workers may be injected by the SDK,
    but this daemon never grants them write/model/network authority itself.
    The service runs until asked to stop, killed, or its leadership is lost.
    On restart, SQLite leases and journals determine what can safely resume.

    Override guidance: inject custom :class:`WorkflowWorker` implementations
    and :class:`IndependentTaskVerifier` implementations, not a replacement
    journal that bypasses mandatory host evidence checks.
    """

    def __init__(self, store: SQLiteRepository, *, project_id: str,
                 settings: CoordinatorSettings | None = None,
                 workers: Mapping[str, WorkflowWorker] | None = None,
                 verifier: IndependentTaskVerifier | None = None):
        if not isinstance(project_id, str) or not project_id.startswith('prj_'):
            raise ValueError('An initialized, project-scoped identity is required')
        if store.get_project(project_id) is None:
            raise PermissionError('Unknown project identity in local registry')
        self.store = store
        self.project_id = project_id
        self.settings = settings or CoordinatorSettings()
        self.journal = WorkflowJournal(store)
        self.leader = LocalCoordinatorLeader(store)
        self.workers = dict(workers or {'prepare': PreparationWorker(store)})
        self.verifier = verifier or IndependentTaskVerifier(store)
        self._supervisor = WorkflowSupervisor(self.journal, self.workers, self.verifier)

    def run(self, *, stop_event: threading.Event | None = None,
            max_seconds: float | None = None, once: bool = False) -> dict:
        """Poll the durable queue while renewing leadership in a separate thread.

        ``once=True`` runs one bounded pass. ``max_seconds`` is useful for local
        certification and graceful maintenance. For unattended days-long use,
        install this explicit CLI process as a restart-on-failure OS service.
        Internal heartbeats never grant a task verification status.
        """
        if max_seconds is not None and (isinstance(max_seconds, bool) or not 1 <= max_seconds <= 604800):
            raise ValueError('Invalid maximum service runtime')
        stop = stop_event or threading.Event()
        owned = self.leader.acquire(ttl_seconds=self.settings.leader_lease_seconds)
        lost = threading.Event()
        heartbeat_shutdown = threading.Event()
        heartbeats = 0
        errors: list[str] = []
        processed = 0
        cycles = 0
        cursor: str | None = None

        def leader_heartbeat() -> None:
            nonlocal heartbeats
            while not heartbeat_shutdown.wait(self.settings.leader_lease_seconds / 3):
                if lost.is_set():
                    return
                try:
                    self.leader.renew(owned, ttl_seconds=self.settings.leader_lease_seconds)
                    heartbeats += 1
                except (CoordinatorConflict, sqlite3.Error, OSError) as exc:
                    errors.append(type(exc).__name__)
                    lost.set()
                    stop.set()
                    return

        background = threading.Thread(target=leader_heartbeat, name='dbound-leader-heartbeat', daemon=True)
        started = time.monotonic()
        background.start()
        try:
            while not stop.is_set() and not lost.is_set():
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    break
                if self.settings.max_rss_mib is not None:
                    try:
                        rss = resident_memory_mib()
                    except (RuntimeError, OSError, ValueError) as exc:
                        raise ResourceBudgetExceeded('RSS measurement unavailable; refusing dispatch') from exc
                    if rss > self.settings.max_rss_mib:
                        raise ResourceBudgetExceeded('Local coordinator RSS budget exceeded')
                active = self.journal.list_active(project_id=self.project_id,
                                                  limit=self.settings.max_workflows_per_cycle,
                                                  after=cursor)
                if active:
                    cursor = active[-1]
                cycles += 1
                for workflow_id in active:
                    if stop.is_set() or lost.is_set():
                        break
                    before = self.journal.status(workflow_id)
                    self._supervisor.run(workflow_id,
                        lease_seconds=self.settings.task_lease_seconds,
                        max_wall_seconds=self.settings.max_wall_seconds_per_workflow,
                        max_dispatch=self.settings.max_dispatch_per_workflow,
                        stop_event=stop)
                    after = self.journal.status(workflow_id)
                    processed += sum(b['state'] != a['state'] for b, a in zip(before['tasks'], after['tasks']))
                if once:
                    break
                stop.wait(self.settings.poll_seconds)
        finally:
            heartbeat_shutdown.set()
            background.join(timeout=2)
            self.leader.release(owned)
        if errors:
            raise CoordinatorConflict('Coordinator lost durable leadership')
        return {'cycles': cycles, 'task_state_changes': processed,
                'leadership_generation': owned.generation,
                'leader_heartbeats': heartbeats, 'stopped': True,
                'task_completed': False}
