"""Viz layout includes scan when enabled in fixture config."""

from pathlib import Path

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.viz_builder import build_flow_graph

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_tiny_plant_graph_has_scan_workers():
    config = load_plant_config(TINY, project_root=ROOT)
    assert config.stages.scan_in.enabled
    result = run_simulation(
        config, project_root=ROOT, seed=2, sample_interval_minutes=15
    )
    graph = build_flow_graph(config, result)
    ids = {
        b["id"]
        for g in graph["groups"]
        for b in g.get("blocks", [])
    }
    assert any(bid.startswith("scan_in:") for bid in ids)
    assert "scan_bypass" not in ids


def test_scan_bypass_when_disabled():
    config = load_plant_config(TINY, project_root=ROOT)
    config.stages.scan_in.enabled = False
    result = run_simulation(
        config, project_root=ROOT, seed=2, sample_interval_minutes=15
    )
    graph = build_flow_graph(config, result)
    ids = {
        b["id"]
        for g in graph["groups"]
        for b in g.get("blocks", [])
    }
    assert "scan_bypass" in ids
    assert not any(bid.startswith("scan_in:") for bid in ids)
