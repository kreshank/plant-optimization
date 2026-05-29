"""KPI collection driven by config objectives."""

from __future__ import annotations

from dataclasses import dataclass, field

from plant_sim.config_models import PlantConfig
from plant_sim.time_utils import deadline_minutes, format_minutes
from plant_sim.unit_tracking import (
    DailyStats,
    QueueTimeSeries,
    UnitMetrics,
    _daily_to_list,
    unit_id,
)

MAX_FLOW_EVENTS = 500_000
MAX_NON_MOVE_FLOW_EVENTS = 20_000


def _stage_summary_dict(
    sm: "StageMetrics",
    sim_duration_minutes: float,
    max_queue: float,
) -> dict:
    items = sm.items_processed
    return {
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
        "max_queue": round(max_queue, 1),
        "worker_count": sm.worker_count,
        "daily": _daily_to_list(sm.daily),
    }


@dataclass
class StageMetrics:
    stage_id: str
    items_processed: int = 0
    total_service_minutes: float = 0.0
    total_wait_minutes: float = 0.0
    labor_role: str | None = None
    worker_count: int = 1
    daily: dict[int, DailyStats] = field(default_factory=dict)

    utilization: float = 0.0

    def record_daily(
        self,
        operating_day: int,
        service_minutes: float,
        wait_minutes: float = 0.0,
        count: int = 1,
    ) -> None:
        d = self.daily.setdefault(operating_day, DailyStats())
        d.items_processed += count
        d.total_service_minutes += service_minutes
        d.total_wait_minutes += wait_minutes

    def compute_utilization(self, sim_duration_minutes: float) -> float:
        """Busy fraction = total service time / (workers * horizon)."""
        if sim_duration_minutes <= 0 or self.worker_count <= 0:
            return 0.0
        available = sim_duration_minutes * self.worker_count
        return min(1.0, self.total_service_minutes / available)


@dataclass
class MetricsCollector:
    config: PlantConfig
    stage_metrics: dict[str, StageMetrics] = field(default_factory=dict)
    items_completed: int = 0
    items_lost: float = 0.0
    items_injected: float = 0.0
    delivery_ready_minutes: float | None = None
    delivery_ready_by_deadline: bool | None = None
    qc_rework_cycles: int = 0
    items_deferred_wash: int = 0
    labor_minutes_by_role: dict[str, float] = field(default_factory=dict)
    max_queue_by_stage: dict[str, float] = field(default_factory=dict)
    unit_metrics: dict[str, UnitMetrics] = field(default_factory=dict)
    queue_time_series: QueueTimeSeries | None = None
    flow_events: list[dict] = field(default_factory=list)
    flow_events_dropped: int = 0
    flow_events_truncated: bool = False
    items_shipped: int = 0
    trucks_departed: int = 0
    partial_trucks: int = 0
    washer_max_queue: dict[str, float] = field(default_factory=dict)
    _sim_duration_minutes: float = 0.0
    playback_start_minutes: float = 0.0
    playback_horizon_minutes: float = 0.0
    _record_flow_events: bool = False
    current_operating_day: int = 1

    @property
    def sim_duration_minutes(self) -> float:
        return self._sim_duration_minutes

    def ensure_stage(self, stage_id: str, workers: int, labor_role: str | None) -> None:
        if stage_id not in self.stage_metrics:
            self.stage_metrics[stage_id] = StageMetrics(
                stage_id=stage_id,
                labor_role=labor_role,
                worker_count=workers,
            )

    def record_stage(
        self,
        stage_id: str,
        workers: int,
        labor_role: str | None,
        service_minutes: float,
        wait_minutes: float = 0.0,
        count: int = 1,
    ) -> None:
        self.ensure_stage(stage_id, workers, labor_role)
        sm = self.stage_metrics[stage_id]
        sm.items_processed += count
        sm.total_service_minutes += service_minutes
        sm.total_wait_minutes += wait_minutes
        sm.worker_count = workers
        sm.record_daily(
            self.current_operating_day,
            service_minutes,
            wait_minutes=wait_minutes,
            count=count,
        )
        if labor_role:
            self.labor_minutes_by_role[labor_role] = (
                self.labor_minutes_by_role.get(labor_role, 0.0) + service_minutes
            )

    def record_queue_depth(self, stage_id: str, depth: float) -> None:
        prev = self.max_queue_by_stage.get(stage_id, 0.0)
        self.max_queue_by_stage[stage_id] = max(prev, depth)

    def ensure_unit(self, stage: str, index: int) -> UnitMetrics:
        uid = unit_id(stage, index)
        if uid not in self.unit_metrics:
            self.unit_metrics[uid] = UnitMetrics(
                unit_id=uid, stage_id=stage, index=index
            )
        return self.unit_metrics[uid]

    def record_unit_queue(self, stage: str, index: int, depth: float) -> None:
        um = self.ensure_unit(stage, index)
        um.max_queue = max(um.max_queue, depth)
        um.queue_sum += depth
        um.queue_samples += 1
        self.record_queue_depth(unit_id(stage, index), depth)
        stage_prev = self.max_queue_by_stage.get(stage, 0.0)
        self.max_queue_by_stage[stage] = max(stage_prev, depth)

    def record_unit_stage(
        self,
        stage: str,
        index: int,
        service_minutes: float,
        wait_minutes: float = 0.0,
    ) -> None:
        um = self.ensure_unit(stage, index)
        um.items_processed += 1
        um.total_service_minutes += service_minutes
        um.total_wait_minutes += wait_minutes
        d = um.daily.setdefault(self.current_operating_day, DailyStats())
        d.items_processed += 1
        d.total_service_minutes += service_minutes
        d.total_wait_minutes += wait_minutes

    def init_time_series(
        self, interval_minutes: float, *, record_flow_events: bool = True
    ) -> None:
        self.queue_time_series = QueueTimeSeries(interval_minutes=interval_minutes)
        self._record_flow_events = record_flow_events

    def log_flow_event(self, sim_minutes: float, kind: str, **fields) -> None:
        if not self._record_flow_events:
            return
        is_move = kind == "move"
        if is_move:
            if len(self.flow_events) >= MAX_FLOW_EVENTS:
                self.flow_events_dropped += 1
                self.flow_events_truncated = True
                return
        else:
            non_move = sum(1 for e in self.flow_events if e.get("kind") != "move")
            if non_move >= MAX_NON_MOVE_FLOW_EVENTS:
                self.flow_events_dropped += 1
                return
            if len(self.flow_events) >= MAX_FLOW_EVENTS:
                self.flow_events_dropped += 1
                self.flow_events_truncated = True
                return
        row: dict = {"t": round(sim_minutes, 3), "kind": kind}
        row.update(fields)
        self.flow_events.append(row)

    def record_washer_queue(self, washer_id: str, depth: float) -> None:
        prev = self.washer_max_queue.get(washer_id, 0.0)
        self.washer_max_queue[washer_id] = max(prev, depth)
        self.record_queue_depth(washer_id, depth)
        self.record_queue_depth("wash", depth)

    def append_time_series_sample(self, sim_minutes: float, snapshot: dict) -> None:
        if self.queue_time_series is None:
            return
        self.queue_time_series.append(sim_minutes, snapshot)
        for uid, data in snapshot.get("units", {}).items():
            parsed = uid.split(":", 1)
            if len(parsed) != 2:
                continue
            stage, idx_s = parsed
            try:
                idx = int(idx_s)
            except ValueError:
                continue
            depth = float(data.get("queue_depth", 0))
            um = self.ensure_unit(stage, idx)
            um.max_queue = max(um.max_queue, depth)
            um.queue_sum += depth
            um.queue_samples += 1

    def record_delivery_ready(self, sim_minutes: float, day_open: float) -> None:
        """Record when delivery scan finishes (next-morning readiness uses day boundary)."""
        _, within = divmod(sim_minutes, 24 * 60)
        if self.delivery_ready_minutes is None or within > (
            self.delivery_ready_minutes % (24 * 60)
        ):
            self.delivery_ready_minutes = sim_minutes
        deadline = deadline_minutes(self.config.objectives.delivery_ready_deadline)
        self.delivery_ready_by_deadline = within <= deadline or within >= day_open

    def total_labor_cost(self) -> float:
        rates = self.config.labor.rates_by_role
        cost = 0.0
        for role, minutes in self.labor_minutes_by_role.items():
            rate = rates.get(role, 0.0)
            cost += (minutes / 60.0) * rate
        return cost

    def finalize_utilization(self, sim_duration_minutes: float) -> None:
        self._sim_duration_minutes = sim_duration_minutes
        for sm in self.stage_metrics.values():
            sm.utilization = sm.compute_utilization(sim_duration_minutes)

    def bottleneck_ranking(self) -> list[tuple[str, float]]:
        ranked = sorted(
            self.stage_metrics.items(),
            key=lambda x: x[1].utilization,
            reverse=True,
        )
        return [(sid, sm.utilization) for sid, sm in ranked]

    def to_dict(self) -> dict:
        deadline = self.config.objectives.delivery_ready_deadline
        ready_str = None
        if self.delivery_ready_minutes is not None:
            _, within = divmod(self.delivery_ready_minutes, 24 * 60)
            ready_str = format_minutes(within)

        return {
            "playback_start_minutes": round(self.playback_start_minutes, 2),
            "playback_horizon_minutes": round(self.playback_horizon_minutes, 2),
            "playback_window_minutes": round(
                max(0.0, self.playback_horizon_minutes - self.playback_start_minutes),
                2,
            ),
            "sim_duration_minutes": round(self._sim_duration_minutes, 2),
            "items_injected": self.items_injected,
            "items_completed": self.items_completed,
            "items_shipped": self.items_shipped,
            "trucks_departed": self.trucks_departed,
            "partial_trucks": self.partial_trucks,
            "items_lost_estimate": round(self.items_lost, 2),
            "flow_events_recorded": len(self.flow_events),
            "flow_events_dropped": self.flow_events_dropped,
            "flow_events_truncated": self.flow_events_truncated,
            "delivery_ready_time": ready_str,
            "delivery_ready_by_deadline": self.delivery_ready_by_deadline,
            "deadline": deadline,
            "total_labor_cost": round(self.total_labor_cost(), 2),
            "qc_rework_cycles": self.qc_rework_cycles,
            "items_deferred_wash": self.items_deferred_wash,
            "economics_capex": self.config.economics.capex,
            "economics_opex_annual": self.config.economics.opex_annual,
            "bottlenecks": [
                {"stage": s, "utilization": round(u, 4)}
                for s, u in self.bottleneck_ranking()[:10]
            ],
            "stages": {
                sid: _stage_summary_dict(
                    sm,
                    self._sim_duration_minutes,
                    self.max_queue_by_stage.get(sid, 0.0),
                )
                for sid, sm in self.stage_metrics.items()
            },
            "labor_minutes_by_role": {
                k: round(v, 2) for k, v in self.labor_minutes_by_role.items()
            },
            "units": {
                uid: um.to_dict(self._sim_duration_minutes)
                for uid, um in self.unit_metrics.items()
            },
            "time_series": (
                self.queue_time_series.to_dict()
                if self.queue_time_series
                else None
            ),
            "flow_events": self.flow_events,
        }
