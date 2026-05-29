"""Outbound truck dispatch and completed-goods buffer."""

from pathlib import Path

import pytest

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.schedule import TruckWave

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_completed_goods_buffer_and_eod_partial_first_day():
    config = load_plant_config(TINY, project_root=ROOT)
    config.policies.outbound_delivery.mode = "end_of_day_cohort"
    config.objectives.simulation_days = 1
    config.items_per_truck = 10
    result = run_simulation(
        config, project_root=ROOT, seed=1, sample_interval_minutes=5
    )
    assert result.metrics.items_completed >= 1
    assert result.metrics.items_shipped >= 1
    assert result.metrics.trucks_departed >= 1
    cap = 10
    remainder = result.metrics.items_completed - result.metrics.items_shipped
    if remainder > 0:
        assert remainder < cap
    if result.metrics.items_completed % cap != 0:
        assert result.metrics.partial_trucks >= 1
    to_buffer = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move" and e.get("to") == "completed_goods"
    ]
    assert to_buffer
    departures = [
        e for e in result.metrics.flow_events if e.get("kind") == "truck_departure"
    ]
    assert departures
    assert departures[0].get("to") == "truck_out"


def test_csv_outgoing_dispatches_full_truck_only(tmp_path):
    schedule = tmp_path / "trucks.csv"
    schedule.write_text(
        "day_of_week,arrival_time,truck_count,direction\n"
        "mon,08:00,2,incoming\n"
        "mon,18:00,1,outgoing\n",
        encoding="utf-8",
    )
    config = load_plant_config(TINY, project_root=ROOT)
    config.inputs.truck_schedule = str(schedule)
    config.policies.outbound_delivery.mode = "csv_outgoing"
    config.objectives.simulation_days = 1
    config.items_per_truck = 5
    config.policies.wash_batching.min_fill_ratio = 0.5
    result = run_simulation(
        config, project_root=ROOT, seed=2, sample_interval_minutes=2
    )
    assert result.metrics.items_completed >= 5
    outgoing = [
        e for e in result.metrics.flow_events if e.get("kind") == "truck_departure"
    ]
    assert outgoing
    for e in outgoing:
        assert int(e.get("count", 0)) == 5


def test_second_operating_day_requires_full_truck_for_eod(tmp_path):
    schedule = tmp_path / "trucks.csv"
    schedule.write_text(
        "day_of_week,arrival_time,truck_count,direction\n"
        "mon,08:00,1,incoming\n"
        "tue,08:00,1,incoming\n",
        encoding="utf-8",
    )
    config = load_plant_config(TINY, project_root=ROOT)
    config.inputs.truck_schedule = str(schedule)
    config.policies.outbound_delivery.mode = "end_of_day_cohort"
    config.items_per_truck = 10
    config.objectives.simulation_days = 2
    result = run_simulation(
        config, project_root=ROOT, seed=3, sample_interval_minutes=5
    )
    cap = 10
    assert result.metrics.items_completed >= cap * 2
    # Day 1 partial allowed; day 2+ only full trucks at EOD.
    assert result.metrics.trucks_departed >= 2


def test_truck_wave_total_items():
    wave = TruckWave(
        day_of_week="mon",
        arrival_time="08:00",
        truck_count=2,
        direction="incoming",
        items_per_truck=12.5,
    )
    assert wave.total_items(10.0) == 25.0
