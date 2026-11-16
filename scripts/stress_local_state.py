#!/usr/bin/env python3
"""Bounded local SQLite stress test with concurrent writes and online snapshots.

This is a *single-machine* durability/throughput probe. It does not simulate
independent physical devices, distributed synchronization, or realistic LLM
loads. Everything is created in a disposable temporary directory; no real
user project, API token, service, model, or cloud dependency is involved.

Example: python scripts/stress_local_state.py --writers 8 --events-per-writer 250 --backups 4
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import argparse
import json
import math
import statistics
import tempfile
import threading
import time
from pathlib import Path

# Stress testing must exercise the installed/source module through its normal
# repository API rather than writing directly into the SQLite schema.
from decisionbound.storage import SQLiteRepository
from decisionbound.state_integrity import create_backup, inspect_database


@dataclass(frozen=True)
class LocalStressReport:
    """Aggregate deterministic counts with observational, not promised, latency."""

    writers: int
    events_per_writer: int
    expected_events: int
    observed_events: int
    backup_attempts: int
    backups_verified: int
    elapsed_seconds: float
    write_p95_ms: float
    write_p99_ms: float
    successful: bool

    def to_dict(self) -> dict:
        """Return portable metric names without local directory paths or secrets."""
        return asdict(self)


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank empirical percentile without external statistics packages."""
    if not values:
        return 0.0
    return sorted(values)[max(0, math.ceil(len(values) * percentile) - 1)]


def stress_local_state(*, writers: int = 8, events_per_writer: int = 250,
                       backups: int = 4) -> LocalStressReport:
    """Exercise concurrent public event writers and read-consistent snapshots.

    Raises:
        ValueError: Dangerous or nonsensical workload dimensions.
        AssertionError: Lost/duplicated events, corrupt backups or broken ordering.
    """
    if (type(writers) is not int or type(events_per_writer) is not int or
            type(backups) is not int or not 1 <= writers <= 32 or
            not 1 <= events_per_writer <= 3000 or not 0 <= backups <= 20 or
            writers * events_per_writer > 60000):
        raise ValueError("Local stress dimensions exceed safe bounds")
    with tempfile.TemporaryDirectory(prefix="dbound-state-stress-") as folder:
        root = Path(folder)
        store = SQLiteRepository(root / "private" / "state.sqlite3")
        samples: list[float] = []
        started = threading.Event()
        start = time.perf_counter()

        def writer(worker_id: int) -> list[float]:
            durations = []
            started.wait()
            for sequence in range(events_per_writer):
                began = time.perf_counter()
                store.record_event(f"worker-{worker_id}", "stress", {"sequence": sequence})
                durations.append((time.perf_counter() - began) * 1000)
            return durations

        def snapshot(index: int) -> int:
            started.wait()
            target = root / f"snapshot-{index}.sqlite3"
            receipt = create_backup(store.path, target)
            report = inspect_database(receipt.path, full=True)
            assert report.ok and report.event_count is not None
            return report.event_count

        with ThreadPoolExecutor(max_workers=writers + max(backups, 1)) as pool:
            futures = [pool.submit(writer, worker_id) for worker_id in range(writers)]
            future_backups = [pool.submit(snapshot, idx) for idx in range(backups)]
            started.set()
            for result in as_completed(futures):
                samples.extend(result.result())
            counts = [future.result() for future in future_backups]
        elapsed = time.perf_counter() - start
        expected = writers * events_per_writer
        health = inspect_database(store.path, full=True)
        assert health.ok and health.event_count == expected, "Committed events were lost or duplicated"
        for worker_id in range(writers):
            events = store.events(f"worker-{worker_id}")
            assert [e["payload"]["sequence"] for e in events] == list(range(events_per_writer)), \
                "Per-worker event order is inconsistent"
        assert all(0 <= count <= expected for count in counts), "Snapshot observed impossible event count"
        return LocalStressReport(
            writers, events_per_writer, expected, health.event_count or 0,
            backups, len(counts), round(elapsed, 3),
            round(_percentile(samples, .95), 3),
            round(_percentile(samples, .99), 3),
            successful=True,
        )


def main() -> None:
    """Run the bounded probe and emit machine-readable one-line JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writers", type=int, default=8)
    parser.add_argument("--events-per-writer", type=int, default=250)
    parser.add_argument("--backups", type=int, default=4)
    args = parser.parse_args()
    report = stress_local_state(writers=args.writers, events_per_writer=args.events_per_writer,
                                backups=args.backups)
    print(json.dumps(report.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
