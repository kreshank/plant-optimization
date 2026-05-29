"""Block-level flow events for visualization particles."""

from pathlib import Path

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_spotter_to_press_line_to_presser_moves():
    config = load_plant_config(TINY, project_root=ROOT)
    config.stages.spotting.workers = 1
    config.routing.after_separation.pct_spotting = 100
    config.routing.after_separation.pct_steam_tunnel = 0
    config.routing.after_separation.pct_jacket_press = 0
    config.routing.after_separation.pct_general_press = 0
    result = run_simulation(
        config, project_root=ROOT, seed=11, sample_interval_minutes=10
    )
    moves = [e for e in result.metrics.flow_events if e.get("kind") == "move"]
    to_conveyor = [e for e in moves if e.get("to") == "press_conveyor"]
    from_conveyor = [e for e in moves if e.get("fr") == "press_conveyor"]
    assert to_conveyor
    assert from_conveyor
    assert any("spotting:0:press" in e.get("fr", "") for e in to_conveyor)
    assert any("general_press:" in e.get("to", "") and ":wait" in e.get("to", "") for e in from_conveyor)


def test_playback_horizon_before_drain_window():
    config = load_plant_config(TINY, project_root=ROOT)
    result = run_simulation(
        config, project_root=ROOT, seed=4, sample_interval_minutes=15
    )
    start = result.metrics.playback_start_minutes
    horizon = result.metrics.playback_horizon_minutes
    assert horizon > 0
    assert horizon < result.metrics.sim_duration_minutes
    assert start >= 0
    assert horizon > start
    ts = result.metrics.queue_time_series
    assert ts is not None
    window = ts.samples_in_playback_window(start, horizon)
    assert window
    assert all(start - 0.01 <= s["t"] <= horizon + 0.01 for s in window)
    from plant_sim.time_utils import day_open_minutes, wash_cutoff_minutes

    one_day = wash_cutoff_minutes(config.calendar) - day_open_minutes(config.calendar)
    assert abs((horizon - start) - one_day) < 1.0
