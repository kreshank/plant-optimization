"""Streaming simulation unit tests (no HTTP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from plant_sim.scenarios import load_scenario
from plant_sim.sim_stream import batches_to_samples, run_simulation_streaming

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def small_config():
    if not (ROOT / "config" / "baseline.yaml").exists():
        pytest.skip("baseline.yaml not present")
    config = load_scenario(None, ROOT)
    config.objectives.simulation_days = 1
    return config


def test_stream_batches_reconstruct_viz_snapshots(small_config) -> None:
    batches: list[dict] = []

    result = run_simulation_streaming(
        small_config,
        ROOT,
        seed=1,
        sample_interval_minutes=5,
        on_batch=batches.append,
        batch_size=3,
        viz_mode=True,
    )
    assert batches
    indices = [step["i"] for b in batches for step in b["steps"]]
    assert indices == list(range(len(indices)))

    direct = result.metrics.queue_time_series.samples
    streamed = batches_to_samples(batches)
    assert len(streamed) == len(direct)
    for i, (s, d) in enumerate(zip(streamed, direct, strict=True)):
        assert s["t"] == d["t"], f"sample {i} t"
        assert s.get("washers") == d.get("washers"), f"sample {i} washers"
        assert s.get("units") == d.get("units"), f"sample {i} units"
        assert s.get("zones") == d.get("zones"), f"sample {i} zones"
