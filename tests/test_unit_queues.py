"""Per-unit queue tracking and time series."""

from pathlib import Path

import pytest

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.viz_builder import build_flow_graph

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def _washer_blocks(graph: dict) -> list[dict]:
    out: list[dict] = []
    for g in graph.get("groups", []):
        for b in g.get("blocks", []):
            if b.get("kind") == "washer":
                out.append(b)
    return out


def test_unit_resources_for_press_and_spotting():
    config = load_plant_config(TINY, project_root=ROOT)
    config.stages.general_press.workers = 2
    config.stages.spotting.workers = 2
    result = run_simulation(
        config,
        project_root=ROOT,
        seed=1,
        sample_interval_minutes=30,
    )
    assert "general_press:0" in result.metrics.unit_metrics
    assert "general_press:1" in result.metrics.unit_metrics
    assert "spotting:0" in result.metrics.unit_metrics
    assert result.metrics.queue_time_series is not None
    assert len(result.metrics.queue_time_series.samples) >= 2


def test_viz_graph_includes_groups_and_time_series():
    config = load_plant_config(TINY, project_root=ROOT)
    result = run_simulation(
        config, project_root=ROOT, seed=2, sample_interval_minutes=60
    )
    graph = build_flow_graph(config, result)
    assert len(graph["groups"]) >= 3
    assert len(_washer_blocks(graph)) >= 1
    assert graph["time_series"] is not None
    assert graph["time_series"]["interval_minutes"] == 60
