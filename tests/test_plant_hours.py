"""Plant day close vs wash intake cutoff and late-shift behavior."""

from pathlib import Path

import pytest

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.time_utils import (
    parse_time_of_day,
    time_to_minutes,
    wash_cutoff_minutes,
    wash_intake_cutoff_minutes,
)

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_intake_cutoff_before_plant_close():
    config = load_plant_config(TINY, project_root=ROOT)
    config.calendar.wash_intake_cutoff_time = "17:00"
    config.calendar.wash_cutoff_time = "19:00"
    assert wash_intake_cutoff_minutes(config.calendar) < wash_cutoff_minutes(
        config.calendar
    )


@pytest.mark.skipif(
    not (ROOT / "config" / "baseline.yaml").exists(),
    reason="local baseline not present",
)
def test_snapshots_exist_between_1700_and_1900():
    from plant_sim.scenarios import load_scenario

    config = load_scenario(None, ROOT)
    assert config.calendar.wash_cutoff_time == "19:00"
    result = run_simulation(
        config, project_root=ROOT, seed=1, sample_interval_minutes=5
    )
    ts = result.metrics.queue_time_series
    assert ts is not None
    t17 = time_to_minutes(parse_time_of_day("17:00"))
    t19 = time_to_minutes(parse_time_of_day("19:00"))
    late_samples = []
    for snap in ts.samples:
        _, within = divmod(snap["t"], 24 * 60)
        if t17 <= within < t19:
            late_samples.append(snap)
    assert late_samples, "expected queue snapshots between 17:00 and 19:00"


@pytest.mark.skipif(
    not (ROOT / "config" / "baseline.yaml").exists(),
    reason="local baseline not present",
)
def test_no_press_moves_during_late_window():
    from plant_sim.scenarios import load_scenario

    config = load_scenario(None, ROOT)
    config.objectives.simulation_days = 1
    result = run_simulation(
        config, project_root=ROOT, seed=2, sample_interval_minutes=1
    )
    t17 = time_to_minutes(parse_time_of_day("17:00"))
    t19 = time_to_minutes(parse_time_of_day("19:00"))
    # Worker stations log :press when actively pressing (shift ends 17:00 on press/spot).
    press_late = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move"
        and str(e.get("to", "")).endswith(":press")
        and any(
            s in str(e.get("to", ""))
            for s in ("general_press", "jacket_press", "spotting")
        )
        and t17 <= divmod(e["t"], 24 * 60)[1] < t19
    ]
    assert not press_late, "press/spot should not actively serve during 17:00–19:00"
