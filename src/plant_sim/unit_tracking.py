"""Per-worker unit IDs and queue time-series for visualization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Stages modeled as one SimPy resource per worker (own queue each).
UNIT_TRACKED_STAGES: tuple[str, ...] = ("spotting", "general_press", "jacket_press")


def unit_id(stage: str, index: int) -> str:
    return f"{stage}:{index}"


def parse_unit_id(uid: str) -> tuple[str, int] | None:
    if ":" not in uid:
        return None
    stage, idx = uid.rsplit(":", 1)
    try:
        return stage, int(idx)
    except ValueError:
        return None


@dataclass
class UnitMetrics:
    unit_id: str
    stage_id: str
    index: int
    items_processed: int = 0
    total_service_minutes: float = 0.0
    total_wait_minutes: float = 0.0
    max_queue: float = 0.0
    queue_sum: float = 0.0
    queue_samples: int = 0

    @property
    def avg_queue(self) -> float:
        if self.queue_samples <= 0:
            return 0.0
        return self.queue_sum / self.queue_samples

    def utilization(self, sim_duration_minutes: float) -> float:
        if sim_duration_minutes <= 0:
            return 0.0
        return min(1.0, self.total_service_minutes / sim_duration_minutes)

    def to_dict(self, sim_duration_minutes: float) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "stage": self.stage_id,
            "index": self.index,
            "label": unit_label(self.stage_id, self.index),
            "items_processed": self.items_processed,
            "utilization": round(self.utilization(sim_duration_minutes), 4),
            "max_queue": round(self.max_queue, 1),
            "avg_queue": round(self.avg_queue, 2),
            "total_wait_minutes": round(self.total_wait_minutes, 1),
        }


def unit_label(stage_id: str, index: int) -> str:
    names = {
        "spotting": "Spotter",
        "general_press": "Press",
        "jacket_press": "Jacket press",
    }
    base = names.get(stage_id, stage_id.replace("_", " ").title())
    return f"{base} {index + 1}"


@dataclass
class QueueTimeSeries:
    interval_minutes: float
    samples: list[dict[str, Any]] = field(default_factory=list)

    def append(self, sim_minutes: float, snapshot: dict[str, Any]) -> None:
        self.samples.append({"t": round(sim_minutes, 2), **snapshot})

    def downsample(self, max_points: int = 600) -> list[dict[str, Any]]:
        if len(self.samples) <= max_points:
            return self.samples
        step = max(1, len(self.samples) // max_points)
        return self.samples[::step]

    def samples_up_to(self, horizon_minutes: float) -> list[dict[str, Any]]:
        if horizon_minutes <= 0:
            return self.samples
        return [s for s in self.samples if s["t"] <= horizon_minutes + 0.001]

    def samples_in_playback_window(
        self, start_minutes: float, end_minutes: float
    ) -> list[dict[str, Any]]:
        if end_minutes <= 0:
            return self.samples
        return [
            s
            for s in self.samples
            if start_minutes - 0.001 <= s["t"] <= end_minutes + 0.001
        ]

    def to_dict(
        self,
        max_points: int = 600,
        *,
        start_minutes: float | None = None,
        horizon_minutes: float | None = None,
    ) -> dict[str, Any]:
        if horizon_minutes is not None and horizon_minutes > 0:
            lo = start_minutes if start_minutes is not None else 0.0
            rows = self.samples_in_playback_window(lo, horizon_minutes)
        elif horizon_minutes is not None:
            rows = self.samples_up_to(horizon_minutes)
        else:
            rows = self.samples
        duration = rows[-1]["t"] if rows else 0.0
        if len(rows) <= max_points:
            sampled = rows
        else:
            step = max(1, len(rows) // max_points)
            sampled = rows[::step]
        lo = start_minutes if start_minutes is not None else 0.0
        hi = horizon_minutes if horizon_minutes is not None else duration
        return {
            "interval_minutes": self.interval_minutes,
            "duration_minutes": duration,
            "playback_start_minutes": lo,
            "playback_horizon_minutes": hi if hi > 0 else duration,
            "playback_window_minutes": max(0.0, hi - lo) if hi > 0 else 0.0,
            "sample_count": len(sampled),
            "samples": sampled,
        }
