"""Policy evaluation from config."""

from __future__ import annotations

from dataclasses import dataclass, field

from plant_sim.config_models import PlantConfig, StaffingRule


@dataclass
class WasherPoolState:
    """Tracks busy count per washer resource type id."""

    busy_by_type: dict[str, int] = field(default_factory=dict)
    count_by_type: dict[str, int] = field(default_factory=dict)

    def register_type(self, resource_id: str, count: int) -> None:
        self.count_by_type[resource_id] = count
        self.busy_by_type.setdefault(resource_id, 0)

    def set_busy(self, resource_id: str, busy: int) -> None:
        self.busy_by_type[resource_id] = busy

    def all_busy(self, resource_ids: list[str]) -> bool:
        for rid in resource_ids:
            total = self.count_by_type.get(rid, 0)
            if total == 0:
                continue
            if self.busy_by_type.get(rid, 0) < total:
                return False
        return True


def evaluate_staffing_mode(
    config: PlantConfig, washer_state: WasherPoolState
) -> str:
    """Return 'normal' or 'reduced' for scan_in workers based on staffing_rules."""
    for rule in config.policies.staffing_rules:
        when = rule.when
        if when.all_resources_busy:
            if washer_state.all_busy(when.all_resources_busy):
                mode = rule.set.get_scan_in_mode()
                if mode == "reduced":
                    return "reduced"
    return "normal"


def effective_loss_rate(config: PlantConfig) -> float:
    lm = config.loss_model
    return lm.base_rate * (1 - lm.coverage * lm.effectiveness)


def ramp_factor(config: PlantConfig, sim_day_index: int) -> float:
    """Multiplier for ramped parameters."""
    if config.ramp is None or config.ramp.press_scan_adoption is None:
        return 1.0
    ramp = config.ramp.press_scan_adoption
    day = sim_day_index + 1
    if ramp.curve == "linear":
        return min(1.0, day / ramp.horizon_days)
    return 1.0
