#!/usr/bin/env python3
"""Offline, dependency-light local coordinator throughput/RSS probe.

A smoke/profiling workload rather than a fleet capacity benchmark. All tasks
are real journalled offline preparation, not model/provider or coding workers.
The script makes a new private temporary state and never touches user projects.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from decisionbound.background import BackgroundCoordinator, CoordinatorSettings, resident_memory_mib
from decisionbound.identity import init_project
from decisionbound.storage import SQLiteRepository
from decisionbound.workflows import WorkflowJournal, WorkflowTask, WorkflowSpec


@dataclass(frozen=True)
class LocalCoordinatorProfile:
    """Measured single-host probe; not an inference throughput or fleet SLO."""
    workflows: int
    tasks_per_workflow: int
    verified_stages: int
    elapsed_seconds: float
    tasks_per_second: float
    cpu_seconds: float
    peak_process_rss_mib: float
    idle_poll_seconds: float
    result: str


def profile_local(*, workflows: int = 3, tasks_per_workflow: int = 8,
                  poll_seconds: float = 0.05) -> LocalCoordinatorProfile:
    """Run finite DAGs and prove conservation of tasks and false completion status."""
    if type(workflows) is not int or not 1 <= workflows <= 15:
        raise ValueError('workflows must be between 1 and 15')
    if type(tasks_per_workflow) is not int or not 1 <= tasks_per_workflow <= 50:
        raise ValueError('tasks_per_workflow must be between 1 and 50')
    if not 0.05 <= poll_seconds <= 2:
        raise ValueError('poll_seconds must be between 0.05 and 2')
    with tempfile.TemporaryDirectory(prefix='dbound-background-profile-') as name:
        root = Path(name)
        repo = root / 'repository'
        repo.mkdir()
        store = SQLiteRepository(root / 'state' / 'state.sqlite3')
        project = init_project(repo, store)
        journal = WorkflowJournal(store)
        ids = []
        for index in range(workflows):
            tasks = tuple(WorkflowTask(f'task_{j:02d}', 'prepare',
                                       'Inspect project engineering conventions')
                          for j in range(tasks_per_workflow))
            ids.append(journal.create(project_path=repo, spec=WorkflowSpec(tasks, max_workers=2),
                                      idempotency_key=f'background_profile_{index}'))
        coordinator = BackgroundCoordinator(store, project_id=project.project_id,
            settings=CoordinatorSettings(poll_seconds=poll_seconds, max_rss_mib=None,
                                         max_workflows_per_cycle=3, max_dispatch_per_workflow=2))
        start = time.perf_counter()
        cpu = time.process_time()
        readings = [resident_memory_mib()]
        deadline = time.monotonic() + 120
        while any(journal.status(x)['state'] == 'running' for x in ids):
            if time.monotonic() > deadline:
                raise RuntimeError('Local background probe exceeded its hard wall guard')
            coordinator.run(once=True)
            readings.append(resident_memory_mib())
        elapsed = max(time.perf_counter() - start, 1e-9)
        cpu_seconds = time.process_time() - cpu
        all_states = [task for x in ids for task in journal.status(x)['tasks']]
        verified = sum(t['state'] == 'verified' for t in all_states)
        if verified != workflows * tasks_per_workflow or any(
            journal.status(x)['task_completed'] for x in ids):
            raise AssertionError('Lost task, unverified stage or false task-completion claim')
        return LocalCoordinatorProfile(workflows, tasks_per_workflow, verified,
            round(elapsed, 4), round(verified / elapsed, 3), round(cpu_seconds, 4),
            round(max(readings), 2), poll_seconds, 'local_preparation_stages_only')


def main() -> None:
    """Print an explicit labeled JSON evidence record for CI artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflows', type=int, default=3)
    parser.add_argument('--tasks', type=int, default=8)
    parser.add_argument('--poll-seconds', type=float, default=0.05)
    args = parser.parse_args()
    print(json.dumps(asdict(profile_local(workflows=args.workflows,
        tasks_per_workflow=args.tasks, poll_seconds=args.poll_seconds)),
        indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
