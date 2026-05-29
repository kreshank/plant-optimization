"""Checkpoint capture/restore tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from plant_sim.checkpoint import Checkpoint, apply_checkpoint, capture_checkpoint
from plant_sim.scenarios import load_scenario
from plant_sim.sim_stream import run_simulation_streaming

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def small_config():
    if not (ROOT / "config" / "baseline.yaml").exists():
        pytest.skip("baseline.yaml not present")
    config = load_scenario(None, ROOT)
    config.objectives.simulation_days = 1
    return config


def test_capture_and_restore_runs_forward(small_config) -> None:
    checkpoints: dict[int, dict] = {}

    def on_cp(i: int, cp: dict) -> None:
        checkpoints[i] = cp

    run_simulation_streaming(
        small_config,
        ROOT,
        seed=3,
        sample_interval_minutes=15,
        on_batch=lambda _b: None,
        batch_size=100,
        on_checkpoint=on_cp,
    )
    assert checkpoints, "expected at least one checkpoint"
    fork = max(checkpoints.keys())
    cp = Checkpoint.from_dict(checkpoints[fork])

    cfg2 = load_scenario(None, ROOT)
    cfg2.objectives.simulation_days = 1
    cfg2.stages.separation.service_time_seconds = (
        cfg2.stages.separation.service_time_seconds or 60
    ) * 1.5

    batches: list[dict] = []

    result = run_simulation_streaming(
        cfg2,
        ROOT,
        seed=3,
        sample_interval_minutes=15,
        on_batch=batches.append,
        batch_size=5,
        initial_checkpoint=cp,
        fork_index=fork,
        branch_id=1,
        generation=1,
    )
    assert result.metrics.queue_time_series is not None
    assert batches
    assert batches[0]["branch_id"] == 1
    assert batches[0]["step_from"] == fork + 1
