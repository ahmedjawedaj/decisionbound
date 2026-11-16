"""Tests for the opt-in, bounded idle background endurance probe."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def get_probe():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'soak_background.py'
    spec = importlib.util.spec_from_file_location('decisionbound_idle_soak', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_idle_soak_exits_and_releases_local_leadership():
    result = get_probe().soak_idle(seconds=1.0, poll_seconds=0.2)
    assert result.leadership_released
    assert 1.0 <= result.elapsed_seconds <= 4.0
    assert result.cycles >= 1
    assert result.mode == 'local_idle_only'
    assert result.rss_mib_at_end > 0
    assert result.cpu_seconds >= 0


def test_idle_soak_rejects_unbounded_settings():
    probe = get_probe()
    for options in ({'seconds': 0}, {'seconds': 260000},
                    {'poll_seconds': 0}, {'poll_seconds': 100}):
        with pytest.raises(ValueError):
            probe.soak_idle(**options)
