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


MAX_VIZ_FLOW_EVENTS = 80_000
MAX_VIZ_NON_MOVE_EVENTS = 4_000


def export_flow_events_for_viz(
    events: list[dict],
    start: float,
    horizon: float,
    *,
    max_moves: int = MAX_VIZ_FLOW_EVENTS,
    max_non_moves: int = MAX_VIZ_NON_MOVE_EVENTS,
) -> tuple[list[dict], dict]:
    """Keep all move events in the playback window; cap batch/truck metadata separately."""
    in_window = [
        e
        for e in events
        if start - 0.001 <= e.get("t", 0) <= horizon + 0.001
    ]
    moves = [e for e in in_window if e.get("kind") == "move"]
    other = [e for e in in_window if e.get("kind") != "move"]
    export_truncated = False
    if len(moves) > max_moves:
        step = max(1, len(moves) // max_moves)
        moves = moves[::step]
        export_truncated = True
    if len(other) > max_non_moves:
        step = max(1, len(other) // max_non_moves)
        other = other[::step]
    exported = sorted(moves + other, key=lambda e: e.get("t", 0))
    meta = {
        "flow_events_exported": len(exported),
        "flow_events_moves_exported": len(moves),
        "flow_events_export_truncated": export_truncated,
        "flow_events_last_t": exported[-1]["t"] if exported else None,
    }
    return exported, meta


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

    events, flow_meta = export_flow_events_for_viz(
        result.metrics.flow_events, start, horizon
    )
    summary = dict(result.summary)
    summary.update(flow_meta)
    if result.metrics.flow_events_truncated:
        summary["flow_events_truncated"] = True
    summary["flow_events_dropped"] = result.metrics.flow_events_dropped

    return {
        "groups": layout["groups"],
        "group_links": layout["group_links"],
        "layout_meta": layout.get("layout_meta", {}),
        "edges": _edges_from_flow_events(config, result),
        "time_series": ts_payload,
        "flow_events": events,
        "summary": summary,
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
            "transfers": config.transfers.model_dump(),
            "model_assumptions": _model_assumptions(config),
        },
    }


def _model_assumptions(config: PlantConfig) -> list[str]:
    """Human-readable limits of the DES model for the viz sidebar."""
    t = config.transfers
    lines = [
        "Moves animate from flow_events; queues come from snapshots.",
        f"Inter-stage delays: after_wash {t.after_wash} min, after_separation "
        f"{t.after_separation} min, after_final_qc {t.after_final_qc} min (no cart travel).",
        "No conveyor WIP between building zones — items jump queue-to-queue after delay.",
        "Utilization divides service time by full sim duration (includes nights/off-shift).",
    ]
    if not config.stages.scan_in.enabled:
        lines.append("Scan-in bypassed: truck → pre-scan / wash backlog directly.")
    mode = config.policies.outbound_delivery.mode
    lines.append(f"Outbound dispatch: {mode} (see completed-waiting / shipped KPIs).")
    return lines


def config_to_jsonable(config: PlantConfig) -> dict[str, Any]:
    return config.model_dump()
