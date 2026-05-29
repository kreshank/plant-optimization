"""Viz graph builder tests."""

from pathlib import Path

import pytest

from plant_sim.engine import run_simulation
from plant_sim.scenarios import load_scenario
from plant_sim.viz_builder import build_flow_graph

ROOT = Path(__file__).resolve().parents[1]


def _block_ids(graph: dict) -> set[str]:
    ids: set[str] = set()
    for g in graph.get("groups", []):
        for b in g.get("blocks", []):
            ids.add(b["id"])
    return ids


@pytest.mark.skipif(
    not (ROOT / "config" / "baseline.yaml").exists(),
    reason="local baseline not present",
)
def test_build_flow_graph_from_baseline():
    config = load_scenario(None, ROOT)
    result = run_simulation(
        config, project_root=ROOT, seed=42, sample_interval_minutes=30
    )
    graph = build_flow_graph(config, result)
    assert len(graph["groups"]) >= 5
    assert len(graph["group_links"]) >= 4
    assert len(graph["edges"]) >= 4
    assert graph["flow_events"]
    ids = _block_ids(graph)
    assert "pre_scan_waiting" in ids
    sep = next(g for g in graph["groups"] if g["id"] == "separation")
    assert sep.get("group_backlog", {}).get("metric") == "zone:separation_backlog"
    assert "separation_backlog" not in ids
    assert "post_scan_waiting" not in ids
    schemas = {g["schema"] for g in graph["groups"]}
    assert "fifo" in schemas
    assert "batch" in schemas
    link_schemas = {lnk["schema"] for lnk in graph["group_links"]}
    assert "split" in link_schemas
    group_ids = {g["id"] for g in graph["groups"]}
    assert "general_press" in group_ids
    inbound = next(g for g in graph["groups"] if g["id"] == "inbound")
    assert inbound.get("group_backlog", {}).get("metric") == "zone:inbound_backlog"
    wash = next(g for g in graph["groups"] if g["id"] == "wash")
    assert wash.get("group_backlog", {}).get("metric") == "zone:post_scan_waiting"
    assert "outbound_scan" in group_ids
    outbound = next(g for g in graph["groups"] if g["id"] == "outbound_scan")
    assert outbound.get("group_backlog", {}).get("metric") == "stage:outbound_scan"
    qc = next(g for g in graph["groups"] if g["id"] == "final_qc")
    assert qc.get("group_backlog", {}).get("metric") == "stage:final_qc"
    delivery = next(g for g in graph["groups"] if g["id"] == "delivery_scan")
    assert delivery.get("group_backlog", {}).get("metric") == "stage:delivery_scan"
    link_pairs = {(lnk["from"], lnk["to"]) for lnk in graph.get("group_links", [])}
    assert ("delivery_scan", "outbound_scan") in link_pairs
    assert ("outbound_scan", "outbound") in link_pairs
    meta = graph.get("layout_meta", {})
    assert meta.get("group_flow") == "vertical"
    assert meta.get("layout_bands")
    orders = [g["pipeline_order"] for g in graph["groups"]]
    assert orders == sorted(orders)
    truck = next(b for g in graph["groups"] for b in g["blocks"] if b["id"] == "truck_in")
    assert truck.get("flow_next") == "pre_scan_waiting"
    if graph.get("time_series"):
        ts = graph["time_series"]
        start = ts.get("playback_start_minutes", 0)
        horizon = ts.get("playback_horizon_minutes", 0)
        assert horizon > start
        for row in ts["samples"]:
            assert start - 0.01 <= row["t"] <= horizon + 0.01
        moves = [e for e in graph["flow_events"] if e.get("kind") == "move"]
        assert moves, "expected move events for playback dots"
        last_move_t = max(e["t"] for e in moves)
        last_sample_t = ts["samples"][-1]["t"]
        assert last_move_t >= last_sample_t - 120, (
            f"move events should cover late playback "
            f"(last_move={last_move_t}, last_sample={last_sample_t})"
        )
    press_g = next(g for g in graph["groups"] if g["id"] == "general_press")
    assert press_g.get("group_backlog", {}).get("metric") == "pool_sum:general_press"
    assert "press_conveyor" not in _block_ids(graph)
    assert "general_press:0" in _block_ids(graph)
    if any(g["id"] == "spotting" for g in graph["groups"]):
        spot = next(g for g in graph["groups"] if g["id"] == "spotting")
        assert spot.get("group_backlog", {}).get("metric") == "pool_sum:spotting"
