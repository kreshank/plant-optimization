"""Calendar breaks and separation worker queues."""

from pathlib import Path

from plant_sim.config_models import CalendarBreak, load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.time_utils import calendar_wait_minutes, time_to_minutes
from plant_sim.time_utils import parse_time_of_day

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


def test_calendar_wait_positive_during_lunch():
    breaks = [(11 * 60 + 30, 12 * 60 + 30)]
    within = 12 * 60
    wait = calendar_wait_minutes(within, None, breaks)
    assert wait > 0
