"""Per-block throughput and timing stats for the visualizer detail panel."""

from __future__ import annotations

from typing import Any

from plant_sim.config_models import PlantConfig
from plant_sim.engine import SimulationResult
from plant_sim.time_utils import (
    operating_window_minutes,
    parse_time_of_day,
    time_to_minutes,
)
from plant_sim.unit_tracking import _daily_to_list, unit_label


def _operating_day_from_t(
    sim_t: float, playback_start: float, window_minutes: float
) -> int:
    if window_minutes <= 0:
        return 1
    if sim_t < playback_start:
        return 1
    return int((sim_t - playback_start) // window_minutes) + 1


def _shift_minutes_per_day(config: PlantConfig, stage_name: str) -> float | None:
    stage = getattr(config.stages, stage_name, None)
    if stage is None or stage.shift is None:
        return None
    start = time_to_minutes(parse_time_of_day(stage.shift.start))
    end = time_to_minutes(parse_time_of_day(stage.shift.end))
    mins = max(0.0, end - start)
    for br in config.calendar.breaks:
        bs = time_to_minutes(parse_time_of_day(br.start))
        be = time_to_minutes(parse_time_of_day(br.end))
        if bs < end and be > start:
            mins = max(0.0, mins - (be - bs))
    return mins


def build_block_statistics(
    config: PlantConfig, result: SimulationResult
) -> dict[str, dict[str, Any]]:
    """Map block id → stats dict for viz detail panel."""
    m = result.metrics
    sim_days = max(config.objectives.simulation_days, 1)
    pb_start = m.playback_start_minutes
    window = operating_window_minutes(config.calendar)
    stats: dict[str, dict[str, Any]] = {}

    for uid, um in m.unit_metrics.items():
        shift_day = _shift_minutes_per_day(config, um.stage_id)
        shift_total = (
            round((shift_day or 0) * sim_days, 1) if shift_day is not None else None
        )
        stats[uid] = {
            "block_id": uid,
            "kind": "worker",
            "stage": um.stage_id,
            "label": unit_label(um.stage_id, um.index),
            "items_processed": um.items_processed,
            "total_service_minutes": round(um.total_service_minutes, 2),
            "total_wait_minutes": round(um.total_wait_minutes, 2),
            "avg_service_seconds": round(um.avg_service_seconds(), 2),
            "avg_wait_seconds": round(um.avg_wait_seconds(), 2),
            "time_worked_hours": round(um.total_service_minutes / 60.0, 2),
            "utilization": round(um.utilization(m.sim_duration_minutes), 4),
            "shift_minutes_per_day": shift_day,
            "shift_capacity_hours": (
                round(shift_total / 60.0, 2) if shift_total is not None else None
            ),
            "max_queue": round(um.max_queue, 1),
            "avg_queue": round(um.avg_queue, 2),
            "daily": um.to_dict(m.sim_duration_minutes)["daily"],
        }

    for sid, sm in m.stage_metrics.items():
        if sid == "wash":
            continue
        items = sm.items_processed
        shift_day = _shift_minutes_per_day(config, sid)
        shift_total = (
            round((shift_day or 0) * sim_days, 1) if shift_day is not None else None
        )
        entry = {
            "block_id": sid,
            "kind": "stage",
            "stage": sid,
            "label": sid.replace("_", " ").title(),
            "items_processed": items,
            "total_service_minutes": round(sm.total_service_minutes, 2),
            "total_wait_minutes": round(sm.total_wait_minutes, 2),
            "avg_service_seconds": round(
                (sm.total_service_minutes / items * 60.0) if items else 0.0, 2
            ),
            "avg_wait_seconds": round(
                (sm.total_wait_minutes / items * 60.0) if items else 0.0, 2
            ),
            "time_worked_hours": round(sm.total_service_minutes / 60.0, 2),
            "utilization": round(sm.utilization, 4),
            "worker_count": sm.worker_count,
            "shift_minutes_per_day": shift_day,
            "shift_capacity_hours": (
                round(shift_total / 60.0, 2) if shift_total is not None else None
            ),
            "max_queue": round(m.max_queue_by_stage.get(sid, 0), 1),
            "daily": _daily_to_list(sm.daily),
        }
        stats[sid] = entry
        if sid == "steam_exit_check":
            stats["steam_exit_check"] = {**entry, "block_id": "steam_exit_check"}

    washer_cycles: dict[str, float] = {}
    for wdef in config.resources.washers:
        for i in range(wdef.count):
            washer_cycles[f"{wdef.id}:{i}"] = wdef.cycle_minutes

    by_washer: dict[str, dict[str, Any]] = {}
    for e in m.flow_events:
        if e.get("kind") != "wash_batch_end":
            continue
        wid = e.get("washer")
        if not wid:
            continue
        n = int(e.get("count", 0))
        if n <= 0:
            continue
        cycle_min = washer_cycles.get(wid, 60.0)
        day = _operating_day_from_t(float(e["t"]), pb_start, window)
        b = by_washer.setdefault(
            wid,
            {"items_processed": 0, "total_service_minutes": 0.0, "daily_map": {}},
        )
        b["items_processed"] += n
        b["total_service_minutes"] += cycle_min
        dmap = b["daily_map"]
        if day not in dmap:
            dmap[day] = {"items_processed": 0, "service_minutes": 0.0}
        dmap[day]["items_processed"] += n
        dmap[day]["service_minutes"] += cycle_min

    for wid, b in by_washer.items():
        items = b["items_processed"]
        svc_min = b["total_service_minutes"]
        daily_rows = [
            {
                "operating_day": day,
                "items_processed": d["items_processed"],
                "service_minutes": round(d["service_minutes"], 2),
                "wait_minutes": 0.0,
                "avg_service_seconds": round(
                    (d["service_minutes"] / d["items_processed"] * 60.0)
                    if d["items_processed"]
                    else 0.0,
                    2,
                ),
                "time_worked_hours": round(d["service_minutes"] / 60.0, 2),
            }
            for day, d in sorted(b["daily_map"].items())
        ]
        stats[wid] = {
            "block_id": wid,
            "kind": "washer",
            "label": wid.replace("_", " "),
            "items_processed": items,
            "total_service_minutes": round(svc_min, 2),
            "avg_service_seconds": round(
                (svc_min / items * 60.0) if items else 0.0, 2
            ),
            "time_worked_hours": round(svc_min / 60.0, 2),
            "daily": daily_rows,
        }

    return stats
