"""Build group-based facility layout for the HTML visualizer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from plant_sim.config_models import PlantConfig
from plant_sim.engine import SimulationResult
from plant_sim.group_layout import build_group_layout


def _productive_hours(config: PlantConfig) -> float:
    from plant_sim.time_utils import day_open_minutes, wash_cutoff_minutes

    open_min = day_open_minutes(config.calendar)
    close_min = wash_cutoff_minutes(config.calendar)
    hours = (close_min - open_min) / 60.0
    if config.calendar.breaks:
        hours -= 1.0
    return max(hours, 1.0)


def _edges_from_flow_events(
    config: PlantConfig, result: SimulationResult
) -> list[dict[str, Any]]:
    events = result.metrics.flow_events
    if not events:
        return []
    sim_days = max(config.objectives.simulation_days, 1)
    hours = _productive_hours(config) * sim_days
    counts: dict[tuple[str, str], float] = defaultdict(float)
    for e in events:
        if e.get("kind") != "move":
            continue
        fr, to = e.get("fr"), e.get("to")
        if fr and to:
            counts[(fr, to)] += float(e.get("n", 1))
    return [
        {
            "from": fr,
            "to": to,
            "rate": round(cnt / hours, 2),
            "count": int(cnt),
        }
        for (fr, to), cnt in sorted(counts.items(), key=lambda x: -x[1])[:40]
        if cnt >= 1
    ]


def build_flow_graph(
    config: PlantConfig, result: SimulationResult
) -> dict[str, Any]:
    r = config.routing.after_separation
    sim_days = max(config.objectives.simulation_days, 1)
    layout = build_group_layout(config, result)

    start = result.metrics.playback_start_minutes
    horizon = result.metrics.playback_horizon_minutes
    if horizon <= 0 and result.metrics.queue_time_series:
        horizon = result.metrics.queue_time_series.samples[-1]["t"]

    ts_payload = None
    if result.metrics.queue_time_series is not None:
        max_pts = min(max(1200, int(200 * sim_days)), 10000)
        ts_payload = result.metrics.queue_time_series.to_dict(
            max_points=max_pts,
            start_minutes=start,
            horizon_minutes=horizon if horizon > 0 else None,
        )

    events = [
        e
        for e in result.metrics.flow_events
        if start - 0.001 <= e.get("t", 0) <= horizon + 0.001
    ]
    if len(events) > 4000:
        step = max(1, len(events) // 4000)
        events = events[::step]

    return {
        "groups": layout["groups"],
        "group_links": layout["group_links"],
        "layout_meta": layout.get("layout_meta", {}),
        "edges": _edges_from_flow_events(config, result),
        "time_series": ts_payload,
        "flow_events": events,
        "summary": result.summary,
        "config_snapshot": {
            "items_per_truck": config.items_per_truck,
            "daily_target": config.objectives.daily_items_target,
            "simulation_days": sim_days,
            "playback_start_minutes": round(start, 2),
            "playback_horizon_minutes": round(horizon, 2),
            "playback_window_minutes": round(max(0.0, horizon - start), 2),
            "sample_interval_minutes": (
                result.metrics.queue_time_series.interval_minutes
                if result.metrics.queue_time_series
                else None
            ),
            "scan_in_enabled": config.stages.scan_in.enabled,
            "washer_count": sum(w.count for w in config.resources.washers),
            "routing": {
                "spotting": r.pct_spotting,
                "steam": r.pct_steam_tunnel,
                "jacket": r.pct_jacket_press,
                "press_direct": r.general_press_pct(),
                "steam_repress": config.routing.after_steam.pct_needs_press,
            },
        },
    }


def config_to_jsonable(config: PlantConfig) -> dict[str, Any]:
    return config.model_dump()
