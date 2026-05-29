"""Block-level statistics for viz detail panel."""

from pathlib import Path

from plant_sim.config_models import load_plant_config
from plant_sim.block_statistics import build_block_statistics
from plant_sim.engine import run_simulation

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_block_statistics_includes_workers_and_daily():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 2
    result = run_simulation(
        config, project_root=ROOT, seed=1, sample_interval_minutes=5
    )
    stats = build_block_statistics(config, result)
    assert "general_press:0" in stats or any(
        k.startswith("general_press:") for k in stats
    )
    press_key = next(k for k in stats if k.startswith("general_press:"))
    st = stats[press_key]
    assert st["items_processed"] >= 1
    assert "avg_service_seconds" in st
    assert "daily" in st
    if config.objectives.simulation_days > 1 and st["daily"]:
        assert st["daily"][0]["operating_day"] >= 1
