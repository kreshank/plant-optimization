"""Calendar breaks and separation worker queues."""

from pathlib import Path

import pytest

from plant_sim.config_models import CalendarBreak, load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.time_utils import (
    calendar_wait_minutes,
    format_minutes,
    minutes_to_time,
    parse_time_of_day,
    time_to_minutes,
)

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_lunch_break_blocks_press_service():
    config = load_plant_config(TINY, project_root=ROOT)
    config.calendar.breaks = [CalendarBreak(start="11:30", end="12:30")]
    config.objectives.simulation_days = 1
    result = run_simulation(
        config, project_root=ROOT, seed=7, sample_interval_minutes=5
    )
    lunch_start = time_to_minutes(parse_time_of_day("11:30"))
    lunch_end = time_to_minutes(parse_time_of_day("12:30"))
    press_moves = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move"
        and ":press" in str(e.get("to", ""))
        and "general_press" in str(e.get("to", ""))
    ]
    for e in press_moves:
        _, within = divmod(e["t"], 24 * 60)
        assert not (lunch_start <= within < lunch_end), (
            f"press work at {within} during lunch"
        )


def test_separation_items_route_to_worker_queues():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    config.stages.separation.service_time_seconds = 0.2
    result = run_simulation(
        config, project_root=ROOT, seed=3, sample_interval_minutes=5
    )
    to_sep_wait = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move"
        and "separation:" in str(e.get("to", ""))
        and ":wait" in str(e.get("to", ""))
    ]
    assert len(to_sep_wait) >= 5


def test_separation_theoretical_max_rate():
    """Sanity: configured service time caps drain speed (not instant)."""
    config = load_plant_config(TINY, project_root=ROOT)
    sep = config.stages.separation
    workers = max(sep.worker_count(), 1)
    svc_min = sep.service_seconds(0) / 60.0
    assert svc_min > 0
    items_per_minute = workers / svc_min
    assert items_per_minute < 500


def test_minutes_to_time_handles_float_drift():
    """Regression: fractional minutes after hour boundary must not yield second=60."""
    within = 11 * 60 + 30 + 59 / 60.0  # 11:30:59 as float minutes
    t = minutes_to_time(within)
    assert t.second < 60
    assert format_minutes(within) == "11:30"


def test_calendar_wait_positive_during_lunch():
    breaks = [(11 * 60 + 30, 12 * 60 + 30)]
    within = 12 * 60
    wait = calendar_wait_minutes(within, None, breaks)
    assert wait > 0


def test_separation_flow_from_backlog_to_wait():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    result = run_simulation(
        config, project_root=ROOT, seed=3, sample_interval_minutes=5
    )
    backlog_to_wait = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move"
        and e.get("fr") == "separation_backlog"
        and "separation:" in str(e.get("to", ""))
        and ":wait" in str(e.get("to", ""))
    ]
    assert backlog_to_wait, "expected separation_backlog → separation:N:wait moves"


@pytest.mark.skipif(
    not (ROOT / "config" / "baseline.yaml").exists(),
    reason="local baseline not present",
)
def test_separation_backlog_during_lunch():
    """Wash release during lunch should queue in separation backlog, not block pre-queue."""
    from plant_sim.scenarios import load_scenario

    config = load_scenario(None, ROOT)
    config.calendar.breaks = [CalendarBreak(start="11:30", end="12:30")]
    config.objectives.simulation_days = 3
    lunch_start = time_to_minutes(parse_time_of_day("11:30"))
    lunch_end = time_to_minutes(parse_time_of_day("12:30"))

    result = run_simulation(
        config, project_root=ROOT, seed=1, sample_interval_minutes=1
    )
    ts = result.metrics.queue_time_series
    assert ts is not None

    backlog_during_lunch = False
    for snap in ts.samples:
        _, within = divmod(snap["t"], 24 * 60)
        if lunch_start <= within < lunch_end:
            if snap.get("zones", {}).get("separation_backlog", 0) > 0:
                backlog_during_lunch = True
                break

    press_during_lunch = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move"
        and "separation:" in str(e.get("to", ""))
        and ":press" in str(e.get("to", ""))
        and lunch_start <= divmod(e["t"], 24 * 60)[1] < lunch_end
    ]

    assert backlog_during_lunch, "expected separation_backlog > 0 during lunch window"
    assert not press_during_lunch, "separators should not press during lunch"
