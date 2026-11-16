"""Ensure local coordinator capacity probes cannot invent completion or costs."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'profile_background.py'
    spec = importlib.util.spec_from_file_location('dbound_background_profile_test', path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    try:
        spec.loader.exec_module(obj)
    finally:
        sys.modules.pop(spec.name, None)
    return obj


def test_local_profiler_preserves_real_journal_stage_counts():
    profile = module().profile_local(workflows=2, tasks_per_workflow=3)
    assert profile.verified_stages == 6
    assert profile.tasks_per_second > 0
    assert profile.cpu_seconds >= 0
    assert profile.peak_process_rss_mib > 0
    assert profile.result == 'local_preparation_stages_only'


def test_profiler_rejects_unbounded_stress_configuration():
    profiler = module()
    for kwargs in ({'workflows': 0}, {'workflows': 10000},
                   {'tasks_per_workflow': 0}, {'tasks_per_workflow': 51},
                   {'poll_seconds': 0}):
        with pytest.raises(ValueError):
            profiler.profile_local(**kwargs)
