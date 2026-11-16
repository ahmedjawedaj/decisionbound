"""Small, deterministic contract for the local concurrency probe."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "stress_local_state.py"


def _module():
    spec = importlib.util.spec_from_file_location("dbound_state_stress", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_workload_bounds_and_integrity(tmp_path):
    module = _module()
    report = module.stress_local_state(writers=3, events_per_writer=30, backups=2)
    assert report.successful
    assert report.expected_events == report.observed_events == 90
    assert report.backup_attempts == report.backups_verified == 2
    assert report.write_p95_ms >= 0 and report.write_p99_ms >= report.write_p95_ms
    for bad in ({"writers": 0}, {"writers": 99}, {"events_per_writer": -2},
                {"events_per_writer": 5000}, {"backups": 21}):
        with pytest.raises(ValueError):
            module.stress_local_state(**bad)
