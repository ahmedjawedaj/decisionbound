"""The per-database queue that keeps competing writer threads from starving."""
from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager

import pytest

from decisionbound.storage import SQLiteRepository, _FairLock, _fair_lock_for


def _wait_until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_waiters_are_admitted_in_arrival_order():
    lock = _FairLock()
    lock.acquire(5)
    order: list[int] = []

    def waiter(index: int) -> None:
        lock.acquire(5)
        order.append(index)
        lock.release()

    threads = []
    for index in range(5):
        thread = threading.Thread(target=waiter, args=(index,))
        thread.start()
        threads.append(thread)
        _wait_until(lambda n=index + 1: len(lock._queue) == n)
    lock.release()
    for thread in threads:
        thread.join(5)
    assert order == [0, 1, 2, 3, 4]


def test_the_holding_thread_can_reacquire_and_others_wait_for_the_last_release():
    lock = _FairLock()
    lock.acquire(1)
    lock.acquire(1)
    got = threading.Event()

    def other() -> None:
        lock.acquire(5)
        got.set()
        lock.release()

    thread = threading.Thread(target=other)
    thread.start()
    _wait_until(lambda: len(lock._queue) == 1)
    lock.release()
    assert not got.wait(0.1), "released one of two holds, so the lock must still be held"
    lock.release()
    assert got.wait(5)
    thread.join(5)


def test_a_waiter_that_times_out_leaves_the_queue_and_does_not_block_later_waiters():
    lock = _FairLock()
    lock.acquire(1)
    failure: list[BaseException] = []
    served = threading.Event()

    def impatient() -> None:
        try:
            lock.acquire(0.05)
        except sqlite3.OperationalError as error:
            failure.append(error)

    def patient() -> None:
        lock.acquire(5)
        served.set()
        lock.release()

    first = threading.Thread(target=impatient)
    first.start()
    _wait_until(lambda: len(lock._queue) == 1)
    second = threading.Thread(target=patient)
    second.start()
    first.join(5)
    assert failure and "database is locked" in str(failure[0])
    assert len(lock._queue) == 1
    lock.release()
    assert served.wait(5)
    second.join(5)
    assert len(lock._queue) == 0


def test_one_queue_is_shared_by_every_repository_object_for_a_file(tmp_path):
    first = SQLiteRepository(tmp_path / "state.sqlite3")
    second = SQLiteRepository(tmp_path / "state.sqlite3")
    other = SQLiteRepository(tmp_path / "other.sqlite3")
    assert _fair_lock_for(first.path) is _fair_lock_for(second.path)
    assert _fair_lock_for(first.path) is not _fair_lock_for(other.path)


def test_slow_commits_do_not_starve_a_writer_past_the_busy_timeout(tmp_path):
    """Regression for a Windows CI failure: 'database is locked' under slow fsync.

    Four threads write through short connections while each commit is slow and
    the busy timeout is far shorter than the whole workload. Without the queue
    SQLite's unfair busy handler lets one thread lose the race for longer than
    the timeout, which raised OperationalError even though work was progressing.
    """
    class SlowCommit(SQLiteRepository):
        @contextmanager
        def _connection(self):
            with super()._connection() as db:
                yield db
                time.sleep(0.015)

    store = SlowCommit(tmp_path / "state.sqlite3")
    # Scale the timeout to this machine's real commit latency. A fixed value is
    # too tight on a slow Windows disk, where one commit can take hundreds of
    # milliseconds and a queued writer legitimately waits for several of them.
    # Twelve commits is still far shorter than the whole workload of 160.
    samples = []
    for _ in range(5):
        began = time.perf_counter()
        store.record_event("calibrate", "calibrate", {})
        samples.append(time.perf_counter() - began)
    store.busy_timeout_seconds = max(0.25, 12 * statistics.median(samples))
    errors: list[BaseException] = []

    def writer(worker: int) -> None:
        try:
            for sequence in range(40):
                store.record_event(f"worker-{worker}", "stress", {"sequence": sequence})
        except BaseException as error:  # noqa: BLE001 - report every failure kind
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors, errors
    for worker in range(4):
        events = store.events(f"worker-{worker}")
        assert [e["payload"]["sequence"] for e in events] == list(range(40))


def test_a_stuck_holder_ends_a_waiters_wait_with_the_sqlite_error(tmp_path):
    store = SQLiteRepository(tmp_path / "state.sqlite3")
    store.busy_timeout_seconds = 0.1
    release = threading.Event()
    held = threading.Event()

    def holder() -> None:
        with store._connection():
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(5)
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.record_event("run", "event", {})
    finally:
        release.set()
        thread.join(5)
