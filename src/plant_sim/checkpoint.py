"""Checkpoint capture and aggregate warm-start restore for streaming branches."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from plant_sim.config_models import PlantConfig


@dataclass
class Checkpoint:
    """Serializable plant state at a snapshot step."""

    step_index: int
    sim_now: float
    snapshot: dict[str, Any]
    rng_state: tuple[Any, ...] | None = None
    wip_counter: int = 0
    items_injected: float = 0.0
    items_completed: int = 0
    items_shipped: int = 0
    trucks_departed: int = 0
    current_operating_day: int = 1
    calendar_day: int = 0
    weekday: int = 0
    operating_count: int = 0
    wip_seeded: bool = False
    first_op_calendar_day: int | None = None
    last_op_calendar_day: int | None = None
    playback_start_minutes: float = 0.0
    playback_horizon_minutes: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "sim_now": self.sim_now,
            "snapshot": self.snapshot,
            "rng_state": self.rng_state,
            "wip_counter": self.wip_counter,
            "items_injected": self.items_injected,
            "items_completed": self.items_completed,
            "items_shipped": self.items_shipped,
            "trucks_departed": self.trucks_departed,
            "current_operating_day": self.current_operating_day,
            "calendar_day": self.calendar_day,
            "weekday": self.weekday,
            "operating_count": self.operating_count,
            "wip_seeded": self.wip_seeded,
            "first_op_calendar_day": self.first_op_calendar_day,
            "last_op_calendar_day": self.last_op_calendar_day,
            "playback_start_minutes": self.playback_start_minutes,
            "playback_horizon_minutes": self.playback_horizon_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            step_index=int(data.get("step_index", 0)),
            sim_now=float(data.get("sim_now", 0)),
            snapshot=copy.deepcopy(data.get("snapshot") or {}),
            rng_state=data.get("rng_state"),
            wip_counter=int(data.get("wip_counter", 0)),
            items_injected=float(data.get("items_injected", 0)),
            items_completed=int(data.get("items_completed", 0)),
            items_shipped=int(data.get("items_shipped", 0)),
            trucks_departed=int(data.get("trucks_departed", 0)),
            current_operating_day=int(data.get("current_operating_day", 1)),
            calendar_day=int(data.get("calendar_day", 0)),
            weekday=int(data.get("weekday", 0)),
            operating_count=int(data.get("operating_count", 0)),
            wip_seeded=bool(data.get("wip_seeded", False)),
            first_op_calendar_day=data.get("first_op_calendar_day"),
            last_op_calendar_day=data.get("last_op_calendar_day"),
            playback_start_minutes=float(data.get("playback_start_minutes", 0)),
            playback_horizon_minutes=float(data.get("playback_horizon_minutes", 0)),
        )


def capture_checkpoint(sim, step_index: int) -> Checkpoint:
    """Capture checkpoint from a running PlantSimulation."""
    snap = sim._capture_queue_snapshot()
    rs = sim.rng.getstate()
    m = sim.metrics
    return Checkpoint(
        step_index=step_index,
        sim_now=float(sim.env.now),
        snapshot=snap,
        rng_state=rs,
        wip_counter=sim._wip_counter,
        items_injected=m.items_injected,
        items_completed=m.items_completed,
        items_shipped=m.items_shipped,
        trucks_departed=m.trucks_departed,
        current_operating_day=m.current_operating_day,
        calendar_day=getattr(sim, "_run_calendar_day", 0),
        weekday=getattr(sim, "_run_weekday", 0),
        operating_count=getattr(sim, "_run_operating_count", 0),
        wip_seeded=getattr(sim, "_run_wip_seeded", False),
        first_op_calendar_day=getattr(sim, "_run_first_op_day", None),
        last_op_calendar_day=getattr(sim, "_run_last_op_day", None),
        playback_start_minutes=m.playback_start_minutes,
        playback_horizon_minutes=m.playback_horizon_minutes,
    )


def apply_checkpoint(sim, cp: Checkpoint) -> None:
    """Hydrate aggregate plant state from checkpoint (approximate warm-start)."""
    sim._wip_counter = cp.wip_counter
    m = sim.metrics
    m.items_injected = cp.items_injected
    m.items_completed = cp.items_completed
    m.items_shipped = cp.items_shipped
    m.trucks_departed = cp.trucks_departed
    m.current_operating_day = cp.current_operating_day
    m.playback_start_minutes = cp.playback_start_minutes
    m.playback_horizon_minutes = cp.playback_horizon_minutes
    if cp.rng_state is not None:
        sim.rng.setstate(cp.rng_state)

    sim._run_calendar_day = cp.calendar_day
    sim._run_weekday = cp.weekday
    sim._run_operating_count = cp.operating_count
    sim._run_wip_seeded = cp.wip_seeded
    sim._run_first_op_day = cp.first_op_calendar_day
    sim._run_last_op_day = cp.last_op_calendar_day
    sim._resume_from_checkpoint = True

    zones = cp.snapshot.get("zones") or {}
    z = sim.zones
    z.trucks_arrived = int(zones.get("trucks_arrived", 0))
    z.pre_scan_waiting = int(zones.get("pre_scan_waiting", 0))
    z.post_scan_waiting = int(zones.get("post_scan_waiting", 0))
    z.separation_backlog = int(zones.get("separation_backlog", 0))
    z.completed_waiting = int(zones.get("completed_waiting", 0))

    for wdef in sim.config.resources.washers:
        for i in range(wdef.count):
            wid = f"{wdef.id}:{i}"
            ws = (cp.snapshot.get("washers") or {}).get(wid) or {}
            ln = next((x for x in sim._washer_lines if x.washer_id == wid), None)
            if ln is None:
                continue
            ln.in_cycle = bool(ws.get("in_cycle", False))
            ln.batch_size = int(ws.get("batch_size", 0))
            ln.cycle_started_at = float(sim.env.now)
            target_bin = int(ws.get("bin_fill", 0))
            ln.bin_items.clear()
            for j in range(target_bin):
                eid = -(cp.step_index * 10000 + j + 1)
                ln.bin_items.append((eid, sim.env.event()))

    for stage, stations in sim._worker_stations.items():
        for st in stations:
            uid = st.station_id
            udata = (cp.snapshot.get("units") or {}).get(uid) or {}
            depth = int(udata.get("queue_depth", 0))
            st.wait_fifo.clear()
            st._waiters.clear()
            for j in range(max(0, depth - st.service.count)):
                eid = -(cp.step_index * 10000 + hash(uid) % 1000 + j)
                st.wait_fifo.append(eid)
                st._waiters[eid] = sim.env.event()

    target = cp.sim_now
    if sim.env.now < target:
        sim.env.run(until=target)
