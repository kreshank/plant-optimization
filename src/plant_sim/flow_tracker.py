"""Per-item flow validation and aggregate pipeline invariants."""

from __future__ import annotations

from dataclasses import dataclass, field

STAGE_PREDECESSORS: dict[str, tuple[str, ...]] = {
    "scan_in": (),
    "wash": ("scan_in",),
    "separation": ("wash",),
    "spotting": ("separation",),
    "steam_tunnel": ("separation",),
    "jacket_press": ("separation",),
    "general_press": ("separation",),
    "final_qc": (),
    "delivery_scan": ("final_qc",),
}

QC_PREDECESSORS: dict[str, tuple[str, ...]] = {
    "spotting_path": ("general_press",),
    "steam_press_path": ("general_press",),
    "steam_direct_path": ("steam_tunnel",),
    "jacket_path": ("jacket_press",),
    "press_path": ("general_press",),
    "rework_path": ("general_press",),
}


@dataclass
class FlowViolation:
    item_id: int
    stage: str
    message: str
    sim_time: float


@dataclass
class FlowTracker:
    scan_in_enabled: bool = True
    violations: list[FlowViolation] = field(default_factory=list)
    _item_completed: dict[int, set[str]] = field(default_factory=dict)
    _item_qc_path: dict[int, str] = field(default_factory=dict)
    _item_in_rework: dict[int, bool] = field(default_factory=dict)
    cumulative_completions: dict[str, int] = field(default_factory=dict)
    cumulative_starts: dict[str, int] = field(default_factory=dict)

    def set_qc_path(self, item_id: int, path: str) -> None:
        self._item_qc_path[item_id] = path

    def mark_rework(self, item_id: int) -> None:
        self._item_in_rework[item_id] = True
        completed = self._item_completed.setdefault(item_id, set())
        completed.discard("general_press")

    def stage_start(self, item_id: int, stage: str, sim_time: float) -> None:
        self.cumulative_starts[stage] = self.cumulative_starts.get(stage, 0) + 1
        completed = self._item_completed.get(item_id, set())

        if stage == "final_qc":
            preds = self._final_qc_predecessors(item_id)
        elif stage == "wash" and not self.scan_in_enabled:
            preds = ()
        elif stage == "spotting" and self._item_in_rework.get(item_id):
            preds = ("final_qc",)
        elif stage == "general_press" and self._item_in_rework.get(item_id):
            preds = ("spotting",)
        elif stage == "general_press":
            preds = self._general_press_predecessors(item_id, completed)
        else:
            preds = STAGE_PREDECESSORS.get(stage, ())
            if stage == "wash" and not self.scan_in_enabled:
                preds = ()

        missing = [p for p in preds if p not in completed]
        if missing:
            self.violations.append(
                FlowViolation(
                    item_id=item_id,
                    stage=stage,
                    message=f"started {stage} before completing {missing}",
                    sim_time=sim_time,
                )
            )

        self._check_aggregate_predecessors(stage, sim_time)

    def stage_complete(self, item_id: int, stage: str, sim_time: float) -> None:
        self._item_completed.setdefault(item_id, set()).add(stage)
        self.cumulative_completions[stage] = (
            self.cumulative_completions.get(stage, 0) + 1
        )

    def _final_qc_predecessors(self, item_id: int) -> tuple[str, ...]:
        path = self._item_qc_path.get(item_id, "press_path")
        return QC_PREDECESSORS.get(path, ("general_press",))

    def _general_press_predecessors(
        self, item_id: int, completed: set[str]
    ) -> tuple[str, ...]:
        if "spotting" in completed and "separation" in completed:
            return ("spotting",)
        if "steam_tunnel" in completed:
            return ("steam_tunnel",)
        if self._item_in_rework.get(item_id):
            return ("spotting",)
        return ("separation",)

    def _check_aggregate_predecessors(self, stage: str, sim_time: float) -> None:
        chain = ["wash", "separation"] if not self.scan_in_enabled else [
            "scan_in",
            "wash",
            "separation",
        ]
        if stage not in chain:
            return
        idx = chain.index(stage)
        if idx == 0:
            return
        upstream = chain[idx - 1]
        up_done = self.cumulative_completions.get(upstream, 0)
        down_started = self.cumulative_starts.get(stage, 0)
        if down_started > up_done:
            self.violations.append(
                FlowViolation(
                    item_id=-1,
                    stage=stage,
                    message=(
                        f"aggregate: {stage} starts ({down_started}) exceed "
                        f"{upstream} completions ({up_done}) at t={sim_time:.1f}"
                    ),
                    sim_time=sim_time,
                )
            )

    @property
    def ok(self) -> bool:
        return len(self.violations) == 0
