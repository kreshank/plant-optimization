#!/usr/bin/env python3
"""Quick import and integration smoke test."""
from pathlib import Path

from plant_sim.block_statistics import build_block_statistics
from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.viz_builder import build_flow_graph

ROOT = Path(__file__).resolve().parents[1]
cfg = load_plant_config(ROOT / "tests/fixtures/tiny_plant.yaml", project_root=ROOT)
r = run_simulation(cfg, project_root=ROOT, seed=1, sample_interval_minutes=5)
bs = build_block_statistics(cfg, r)
g = build_flow_graph(cfg, r)
assert "block_statistics" in g
assert len(bs) > 0
print("import smoke OK", len(bs), "blocks")
