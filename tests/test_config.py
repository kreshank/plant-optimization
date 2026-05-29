"""Config validation tests (uses tests/fixtures only)."""

from pathlib import Path

import pytest
import yaml

from plant_sim.config_models import PlantConfig, load_plant_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def test_baseline_fixture_loads():
    config = load_plant_config(FIXTURES / "tiny_plant.yaml", project_root=ROOT)
    assert config.items_per_truck == 10


def test_routing_sums():
    config = load_plant_config(FIXTURES / "tiny_plant.yaml", project_root=ROOT)
    r = config.routing.after_separation
    general = r.general_press_pct()
    total = r.pct_spotting + r.pct_steam_tunnel + r.pct_jacket_press + general
    assert abs(total - 100) < 0.02


def test_scenario_overlay_merge():
    base = load_plant_config(FIXTURES / "tiny_plant.yaml", project_root=ROOT)
    merged = load_plant_config(
        FIXTURES / "tiny_plant.yaml",
        FIXTURES / "overlay_scenario.yaml",
        project_root=ROOT,
    )
    assert merged.loss_model.coverage == 0.5
    assert merged.economics.capex == 1000
    assert base.loss_model.coverage != merged.loss_model.coverage


def test_invalid_routing_raises():
    data = yaml.safe_load((FIXTURES / "tiny_plant.yaml").read_text(encoding="utf-8"))
    data["routing"]["after_separation"]["pct_steam_tunnel"] = 90
    with pytest.raises(Exception):
        PlantConfig.model_validate(data)


def test_truck_schedule_path_exists():
    config = load_plant_config(FIXTURES / "tiny_plant.yaml", project_root=ROOT)
    path = ROOT / config.inputs.truck_schedule
    assert path.exists()
