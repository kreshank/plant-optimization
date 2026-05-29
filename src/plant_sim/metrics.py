"""KPI collection driven by config objectives."""

from __future__ import annotations

from dataclasses import dataclass, field

from plant_sim.config_models import PlantConfig
from plant_sim.time_utils import deadline_minutes, format_minutes


@dataclass
class StageMetrics:
    stage_id: str
    items_processed: int = 0
    total_service_minutes: float = 0.0
    total_wait_minutes: float = 0.0
    labor_role: str | None = None
    worker_count: int = 1

    utilization: float = 0.0

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
        if labor_role:
            self.labor_minutes_by_role[labor_role] = (
                self.labor_minutes_by_role.get(labor_role, 0.0) + service_minutes
            )

    def record_queue_depth(self, stage_id: str, depth: float) -> None:
        prev = self.max_queue_by_stage.get(stage_id, 0.0)
        self.max_queue_by_stage[stage_id] = max(prev, depth)

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
            "items_injected": self.items_injected,
            "items_completed": self.items_completed,
            "items_lost_estimate": round(self.items_lost, 2),
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
                sid: {
                    "items_processed": sm.items_processed,
                    "utilization": round(sm.utilization, 4),
                    "max_queue": self.max_queue_by_stage.get(sid, 0),
                }
                for sid, sm in self.stage_metrics.items()
            },
            "labor_minutes_by_role": {
                k: round(v, 2) for k, v in self.labor_minutes_by_role.items()
            },
        }
