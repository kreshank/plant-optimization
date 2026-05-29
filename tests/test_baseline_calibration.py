"""Tests for calibrated baseline config (local gitignored config/)."""

from pathlib import Path

import pytest

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "config" / "baseline.yaml"


@pytest.mark.skipif(not BASELINE.exists(), reason="local baseline not present")
def test_calibrated_baseline_loads():
    config = load_plant_config(BASELINE, project_root=ROOT)
    assert config.items_per_truck == pytest.approx(235.29, rel=0.01)
    assert config.stages.scan_in.enabled is False
    assert config.routing.after_separation.pct_steam_tunnel == 68


@pytest.mark.skipif(not BASELINE.exists(), reason="local baseline not present")
def test_calibrated_baseline_runs_without_flow_violations():
    config = load_plant_config(BASELINE, project_root=ROOT)
    config.objectives.simulation_days = 1
    result = run_simulation(config, project_root=ROOT, seed=1, track_flow=True)
    assert result.flow is not None
    assert result.flow.ok, result.flow.violations[:5]
    assert result.metrics.items_injected == pytest.approx(4000, rel=0.02)
