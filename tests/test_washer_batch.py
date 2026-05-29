"""Washer batching: full cycle hold, basket queue in front, fill-first routing."""

from pathlib import Path

import pytest

from plant_sim.config_models import load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.zones import pick_fill_first_washer

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "tests" / "fixtures" / "tiny_plant.yaml"


def test_washer_batch_events_logged():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    result = run_simulation(
        config, project_root=ROOT, seed=3, sample_interval_minutes=30
    )
    kinds = {e["kind"] for e in result.metrics.flow_events}
    assert "wash_batch_start" in kinds
    assert "wash_batch_end" in kinds
    moves = [e for e in result.metrics.flow_events if e["kind"] == "move"]
    press_moves = [e for e in moves if "general_press:" in e.get("to", "")]
    assert len(press_moves) > 0


def test_fill_first_prefers_active_bin():
    """Second washer should not receive items while the first bin is still filling."""
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    config.resources.washers[0].count = 2
    config.resources.washers[0].capacity_items = 8
    config.policies.wash_batching.allow_partial_load = False
    result = run_simulation(
        config, project_root=ROOT, seed=9, sample_interval_minutes=5
    )
    bin_moves = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "move" and str(e.get("to", "")).endswith(":bin")
    ]
    assert bin_moves
    first_washer = bin_moves[0]["to"].rsplit(":", 1)[0] + ":bin"
    first_washer_id = first_washer.replace(":bin", "")
    count_first = sum(1 for e in bin_moves if e["to"].startswith(first_washer_id))
    count_second = len(bin_moves) - count_first
    assert count_first >= 8
    if count_second > 0:
        assert count_first >= config.resources.washers[0].capacity_items


def test_pick_fill_first_washer_unit():
    class _Line:
        def __init__(self, wid: str, fill: int, cap: int, cycle: bool = False):
            self.washer_id = wid
            self.bin_items = [0] * fill
            self._pending = []
            self.in_cycle = cycle
            self.batch_size = 0
            self.wdef = type("W", (), {"capacity_items": cap})()

        @property
        def bin_fill(self):
            return len(self.bin_items)

        def spare_capacity(self) -> int:
            if self.in_cycle:
                return 0
            return self.wdef.capacity_items - self.bin_fill - len(self._pending)

    lines = [
        _Line("a:0", 3, 10),
        _Line("a:1", 0, 10),
        _Line("a:2", 0, 10, cycle=True),
    ]
    pool = __import__("plant_sim.policies", fromlist=["WasherPoolState"]).WasherPoolState()
    pick = pick_fill_first_washer(lines, pool)
    assert pick is not None
    assert pick.washer_id == "a:0"
    empty = [_Line("b:0", 0, 10), _Line("b:1", 0, 20)]
    first = pick_fill_first_washer(empty, pool)
    second = pick_fill_first_washer(empty, pool)
    assert first is not None and second is not None
    assert first.washer_id == "b:1"
    assert second.washer_id == "b:0"


def test_bins_never_exceed_capacity():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    config.resources.washers[0].count = 3
    config.resources.washers[0].capacity_items = 10
    result = run_simulation(
        config, project_root=ROOT, seed=11, sample_interval_minutes=5
    )
    ts = result.metrics.queue_time_series
    assert ts is not None
    for snap in ts.samples:
        for wid, ws in (snap.get("washers") or {}).items():
            cap = ws.get("bin_capacity", 1)
            fill = ws.get("bin_fill", 0)
            pending = ws.get("pending_to_bin", 0)
            assert fill + pending <= cap, f"{wid} overfill {fill}+{pending}>{cap}"


def test_no_partial_batch_while_post_scan_has_reserve():
    """With clothes waiting at post_scan, washers must not start a 1-item cycle."""
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    config.resources.washers[0].count = 1
    config.resources.washers[0].capacity_items = 10
    config.policies.wash_batching.allow_partial_load = True
    result = run_simulation(
        config, project_root=ROOT, seed=13, sample_interval_minutes=2
    )
    starts = [
        e
        for e in result.metrics.flow_events
        if e.get("kind") == "wash_batch_start"
    ]
    assert starts
    for e in starts:
        count = e.get("count", 0)
        t = e.get("t", 0)
        snap = next(
            (
                s
                for s in result.metrics.queue_time_series.samples
                if abs(s["t"] - t) < 3
            ),
            None,
        )
        post_scan = snap.get("zones", {}).get("post_scan_waiting", 0) if snap else 0
        if post_scan > 0 and count < config.resources.washers[0].capacity_items:
            pytest.fail(
                f"partial batch {count} at t={t} while post_scan_waiting={post_scan}"
            )


def test_post_scan_waiting_can_hold_reserve():
    config = load_plant_config(TINY, project_root=ROOT)
    config.objectives.simulation_days = 1
    config.resources.washers[0].count = 1
    config.resources.washers[0].capacity_items = 5
    config.resources.washers[0].cycle_minutes = 120
    result = run_simulation(
        config, project_root=ROOT, seed=7, sample_interval_minutes=1
    )
    ts = result.metrics.queue_time_series
    assert ts is not None
    peaks = [
        s.get("zones", {}).get("post_scan_waiting", 0) for s in ts.samples
    ]
    assert max(peaks) > 0


@pytest.mark.skipif(
    not (ROOT / "config" / "baseline.yaml").exists(),
    reason="local baseline not present",
)
def test_baseline_washer_units_in_graph():
    from plant_sim.scenarios import load_scenario
    from plant_sim.viz_builder import build_flow_graph

    config = load_scenario(None, ROOT)
    config.objectives.simulation_days = 1
    result = run_simulation(
        config, project_root=ROOT, seed=1, sample_interval_minutes=30
    )
    graph = build_flow_graph(config, result)
    washers = [
        b
        for g in graph["groups"]
        for b in g.get("blocks", [])
        if b.get("kind") == "washer"
    ]
    assert len(washers) >= 4
    assert any("general_press" in e["to"] for e in graph["edges"])
    group_ids = {g["id"] for g in graph["groups"]}
    assert "general_press" in group_ids
    assert "layout_meta" in graph
    assert graph["layout_meta"].get("canvas_width_px", 0) > 500
