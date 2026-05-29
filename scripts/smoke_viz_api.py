#!/usr/bin/env python3
"""Smoke test: build_flow_graph serializes and matches viz contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.engine import run_simulation
from plant_sim.scenarios import load_scenario
from plant_sim.viz_builder import build_flow_graph


def main() -> int:
    config = load_scenario(None, ROOT)
    result = run_simulation(
        config, project_root=ROOT, seed=42, sample_interval_minutes=1
    )
    graph = build_flow_graph(config, result)
    body = json.dumps(graph)
    assert "groups" in graph and len(graph["groups"]) >= 5
    assert graph.get("time_series") is not None
    assert len(graph["time_series"]["samples"]) >= 1
    group_ids = {g["id"] for g in graph["groups"]}
    for gid in (
        "wash",
        "final_qc",
        "delivery_scan",
        "outbound_scan",
    ):
        assert gid in group_ids, f"missing group {gid}"
    wash = next(g for g in graph["groups"] if g["id"] == "wash")
    assert wash.get("group_backlog", {}).get("metric") == "zone:post_scan_waiting"
    qc = next(g for g in graph["groups"] if g["id"] == "final_qc")
    assert qc.get("group_backlog", {}).get("metric") == "stage:final_qc"
    delivery = next(g for g in graph["groups"] if g["id"] == "delivery_scan")
    assert delivery.get("group_backlog", {}).get("metric") == "stage:delivery_scan"
    link_pairs = {(lnk["from"], lnk["to"]) for lnk in graph.get("group_links", [])}
    assert ("delivery_scan", "outbound_scan") in link_pairs
    for g in graph["groups"]:
        assert "id" in g and "blocks" in g and "width_px" in g
        if g.get("group_backlog"):
            assert g.get("backlog_panel_px", 0) > 0
            assert "metric" in g["group_backlog"]
    ts = graph["time_series"]["samples"][0]
    assert "inbound_backlog" in ts.get("zones", {})
    print(f"ok: {len(graph['groups'])} groups, {len(body)} bytes JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
