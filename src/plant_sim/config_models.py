"""Pydantic models for plant configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class CalendarBreak(BaseModel):
    start: str
    end: str


class CalendarConfig(BaseModel):
    operating_days: list[str]
    day_open_time: str
    """Last time new garments may enter the wash pipeline (defer until next open)."""
    wash_intake_cutoff_time: str = "17:00"
    """Plant day close: snapshots, playback, and default outbound dispatch."""
    wash_cutoff_time: str
    breaks: list[CalendarBreak] = Field(default_factory=list)


class WasherResourceDef(BaseModel):
    id: str
    cycle_minutes: float = Field(gt=0)
    capacity_items: int = Field(gt=0)
    count: int = Field(default=1, ge=1)


class ResourcesConfig(BaseModel):
    washers: list[WasherResourceDef]


class StageWorkers(BaseModel):
    normal: int | None = None
    reduced: int | None = None
    count: int | None = None

    def resolve(self, mode: str = "normal") -> int:
        if mode == "reduced" and self.reduced is not None:
            return self.reduced
        if mode == "normal" and self.normal is not None:
            return self.normal
        if self.count is not None:
            return self.count
        if self.normal is not None:
            return self.normal
        raise ValueError("Stage workers config must define count, normal, or reduced")


class StageShift(BaseModel):
    start: str
    end: str


class StageConfig(BaseModel):
    enabled: bool = True
    workers: StageWorkers | int | None = None
    service_time_seconds: float | None = Field(default=None, gt=0)
    throughput_items_per_hour: float | None = Field(default=None, gt=0)
    post_check_seconds: float | None = Field(default=None, ge=0)
    requires_scan: bool = False
    defect_rate: float | None = Field(default=None, ge=0, le=1)
    labor_role: str | None = None
    shift: StageShift | None = None

    @field_validator("workers", mode="before")
    @classmethod
    def coerce_workers(cls, v: Any) -> Any:
        if isinstance(v, int):
            return StageWorkers(count=v)
        return v

    def worker_count(self, mode: str = "normal") -> int:
        if self.workers is None:
            return 1
        if isinstance(self.workers, int):
            return self.workers
        return self.workers.resolve(mode)

    def service_seconds(self, scan_seconds: float = 0.0) -> float:
        if self.service_time_seconds is not None:
            base = self.service_time_seconds
            if self.requires_scan:
                return base + scan_seconds
            return base
        if self.throughput_items_per_hour is not None:
            base = 3600.0 / self.throughput_items_per_hour
            if self.requires_scan:
                return base + scan_seconds
            return base
        raise ValueError("Stage must define service_time_seconds or throughput_items_per_hour")


class StagesConfig(BaseModel):
    scan_in: StageConfig
    separation: StageConfig
    steam_tunnel: StageConfig
    jacket_press: StageConfig
    general_press: StageConfig
    final_qc: StageConfig
    spotting: StageConfig
    delivery_scan: StageConfig
    outbound_scan: StageConfig


class RoutingAfterSeparation(BaseModel):
    pct_spotting: float = Field(ge=0, le=100)
    pct_steam_tunnel: float = Field(ge=0, le=100)
    pct_jacket_press: float = Field(ge=0, le=100)
    pct_general_press: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_split(self) -> RoutingAfterSeparation:
        total = self.pct_spotting + self.pct_steam_tunnel + self.pct_jacket_press
        if self.pct_general_press is not None:
            if abs(total + self.pct_general_press - 100) > 0.01:
                raise ValueError(
                    "routing.after_separation percentages must sum to 100"
                )
        else:
            non_spot = 100 - self.pct_spotting
            rest = self.pct_steam_tunnel + self.pct_jacket_press
            if rest > non_spot + 0.01:
                raise ValueError(
                    "steam + jacket routing exceeds non-spotting share"
                )
        return self

    def general_press_pct(self) -> float:
        if self.pct_general_press is not None:
            return self.pct_general_press
        return 100 - self.pct_spotting - self.pct_steam_tunnel - self.pct_jacket_press


class RoutingAfterSteam(BaseModel):
    pct_needs_press: float = Field(ge=0, le=100)


class RoutingConfig(BaseModel):
    after_separation: RoutingAfterSeparation
    after_steam: RoutingAfterSteam


class WashBatchingPolicy(BaseModel):
    start_when_full_or_idle: bool = True
    allow_partial_load: bool = True
    min_fill_ratio: float = Field(default=0.85, ge=0.0, le=1.0)


class WashCutoffPolicy(BaseModel):
    defer_to: str = "next_day_open"


class StaffingRuleWhen(BaseModel):
    all_resources_busy: list[str] | None = None


class StaffingRuleSet(BaseModel):
    stages: dict[str, Any] | None = None

    def get_scan_in_mode(self) -> str | None:
        if self.stages and "scan_in" in self.stages:
            workers = self.stages["scan_in"].get("workers", {})
            if isinstance(workers, dict) and "active" in workers:
                return str(workers["active"])
        return None


class StaffingRule(BaseModel):
    name: str
    when: StaffingRuleWhen
    set: StaffingRuleSet


class QcReworkPolicy(BaseModel):
    max_cycles: int = Field(default=3, ge=1)
    on_defect_goto: str = "spotting"
    then: str = "general_press"


class OutboundDeliveryPolicy(BaseModel):
    """end_of_day_cohort | csv_outgoing | both — see engine outbound dispatch."""

    mode: str = "both"
    dispatch_time: str | None = None


class PoliciesConfig(BaseModel):
    wash_batching: WashBatchingPolicy = Field(default_factory=WashBatchingPolicy)
    wash_cutoff: WashCutoffPolicy = Field(default_factory=WashCutoffPolicy)
    staffing_rules: list[StaffingRule] = Field(default_factory=list)
    qc_rework: QcReworkPolicy = Field(default_factory=QcReworkPolicy)
    outbound_delivery: OutboundDeliveryPolicy = Field(
        default_factory=OutboundDeliveryPolicy
    )


class LossModelConfig(BaseModel):
    base_rate: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    effectiveness: float = Field(ge=0, le=1)


class LaborConfig(BaseModel):
    rates_by_role: dict[str, float] = Field(default_factory=dict)


class EconomicsConfig(BaseModel):
    capex: float = 0.0
    opex_annual: float = 0.0
    horizon_years: float = 5.0


class InputsConfig(BaseModel):
    truck_schedule: str


class TransfersConfig(BaseModel):
    """Delays between sections, in sim minutes (e.g. 0.083 ≈ 5 seconds)."""

    after_scan_in: float = Field(default=0, ge=0)
    to_wash: float = Field(default=0, ge=0)
    after_wash: float = Field(default=0, ge=0)
    after_separation: float = Field(default=0, ge=0)
    after_spotting: float = Field(default=0, ge=0)
    after_steam_tunnel: float = Field(default=0, ge=0)
    after_jacket_press: float = Field(default=0, ge=0)
    after_general_press: float = Field(default=0, ge=0)
    after_final_qc: float = Field(default=0, ge=0)
    after_delivery_scan: float = Field(default=0, ge=0)
    after_outbound_scan: float = Field(default=0, ge=0)


class WipConfig(BaseModel):
    initial_by_stage: dict[str, int] = Field(default_factory=dict)


class ObjectivesConfig(BaseModel):
    delivery_ready_deadline: str = "06:00"
    simulation_days: int = Field(default=6, ge=1)
    daily_items_target: float | None = None


class OptimizationBounds(BaseModel):
    bounds: dict[str, list[float | int]] = Field(default_factory=dict)


class SensitivityConfig(BaseModel):
    ranges: dict[str, list[float]] = Field(default_factory=dict)


class RampPressScanAdoption(BaseModel):
    horizon_days: int = Field(ge=1)
    curve: str = "linear"
    target_coverage: float = Field(ge=0, le=1)


class RampConfig(BaseModel):
    press_scan_adoption: RampPressScanAdoption | None = None


class ConstraintsConfig(BaseModel):
    max_labor_cost_daily: float | None = None
    min_items_completed_daily: int | None = None


class PlantConfig(BaseModel):
    calendar: CalendarConfig
    items_per_truck: float = Field(gt=0)
    resources: ResourcesConfig
    stages: StagesConfig
    scan_seconds_per_item: float = Field(default=0, ge=0)
    routing: RoutingConfig
    transfers: TransfersConfig = Field(default_factory=TransfersConfig)
    wip: WipConfig = Field(default_factory=WipConfig)
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    loss_model: LossModelConfig
    labor: LaborConfig = Field(default_factory=LaborConfig)
    economics: EconomicsConfig = Field(default_factory=EconomicsConfig)
    inputs: InputsConfig
    objectives: ObjectivesConfig = Field(default_factory=ObjectivesConfig)
    optimization: OptimizationBounds = Field(default_factory=OptimizationBounds)
    sensitivity: SensitivityConfig = Field(default_factory=SensitivityConfig)
    ramp: RampConfig | None = None
    constraints: ConstraintsConfig | None = None

    @model_validator(mode="after")
    def validate_washer_ids(self) -> PlantConfig:
        ids = [w.id for w in self.resources.washers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate washer resource ids")
        for rule in self.policies.staffing_rules:
            if rule.when.all_resources_busy:
                for rid in rule.when.all_resources_busy:
                    if rid not in ids:
                        raise ValueError(
                            f"staffing rule references unknown resource id: {rid}"
                        )
        return self

    def all_washer_resource_ids(self) -> list[str]:
        return [w.id for w in self.resources.washers]


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_plant_config(
    baseline_path: Path,
    scenario_path: Path | None = None,
    project_root: Path | None = None,
) -> PlantConfig:
    root = project_root or baseline_path.parent.parent
    data = load_yaml(baseline_path)
    if scenario_path is not None:
        overlay = load_yaml(scenario_path)
        data = _deep_merge(data, overlay)
    return PlantConfig.model_validate(data)


def config_paths_from_root(root: Path) -> tuple[Path, Path]:
    return root / "config" / "baseline.yaml", root / "config" / "scenarios"
