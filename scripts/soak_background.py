#!/usr/bin/env python3
"""Reproducible local coordinator idle/soak probe for user-host endurance tests.

Run on an initialized test repository with a *dedicated* test DB only. Never
mount real credentials or production databases. This probe deliberately starts
no model, network or coding worker. It can run for hours under a service
manager and reports CPU, RSS and leadership fencing state after graceful exit.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
import time

from decisionbound.background import BackgroundCoordinator, CoordinatorSettings, resident_memory_mib
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository


@dataclass(frozen=True)
class IdleSoakReport:
    """Measured finite idle workload, never a proof of distributed reliability."""
    scheduled_seconds: float
    elapsed_seconds: float
    cpu_seconds: float
    mean_cpu_percent: float
    rss_mib_at_end: float
    cycles: int
    leadership_released: bool
    mode: str = 'local_idle_only'


def soak_idle(*, seconds: float = 10.0, poll_seconds: float = 2.0) -> IdleSoakReport:
    """Run a separate temporary SQLite-backed coordinator for a finite duration."""
    if (isinstance(seconds, bool) or not 1 <= seconds <= 259200 or
            isinstance(poll_seconds, bool) or not 0.05 <= poll_seconds <= 60):
        raise ValueError('Soak limits must be finite and bounded (max three days)')
    with tempfile.TemporaryDirectory(prefix='dbound-idle-soak-') as directory:
        root = Path(directory)
        project_root = root / 'repo'
        project_root.mkdir()
        store = SQLiteRepository(root / 'state' / 'state.sqlite3')
        project = init_project(project_root, store)
        runner = BackgroundCoordinator(store, project_id=project.project_id,
            settings=CoordinatorSettings(poll_seconds=poll_seconds, max_rss_mib=None))
        begin = time.perf_counter()
        cpu = time.process_time()
        result = runner.run(max_seconds=seconds)
        elapsed = time.perf_counter() - begin
        consumed = time.process_time() - cpu
        return IdleSoakReport(seconds, round(elapsed, 4), round(consumed, 4),
            round(100 * consumed / max(elapsed, 1e-9), 3),
            round(resident_memory_mib(), 2), result['cycles'],
            not runner.leader.status()['running'])


def main() -> None:
    """Print a privacy-safe JSON result for a CI or operator runbook."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--poll-seconds', type=float, default=2)
    args = parser.parse_args()
    print(json.dumps(asdict(soak_idle(seconds=args.seconds, poll_seconds=args.poll_seconds)),
                     indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
