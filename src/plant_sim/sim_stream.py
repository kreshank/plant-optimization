"""Streaming simulation: batched snapshot deltas + flow events."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plant_sim.checkpoint import Checkpoint, apply_checkpoint, capture_checkpoint
from plant_sim.config_models import PlantConfig
from plant_sim.engine import PlantSimulation, SimulationResult, SimulationCancelled
from plant_sim.snapshot_delta import diff_snapshot


@dataclass
class SimBatch:
    branch_id: int
    generation: int
    step_from: int
    step_to: int
    steps: list[dict[str, Any]]
    flow_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "generation": self.generation,
            "step_from": self.step_from,
            "step_to": self.step_to,
            "steps": self.steps,
            "flow_events": self.flow_events,
        }


class StreamRecorder:
    """Accumulate per-step snapshots; emit sim_batch payloads."""

    def __init__(
        self,
        on_batch: Callable[[dict[str, Any]], None],
        *,
        batch_size: int = 20,
        branch_id: int = 0,
        generation: int = 0,
        on_checkpoint: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.on_batch = on_batch
        self.on_checkpoint = on_checkpoint
        self.batch_size = max(1, batch_size)
        self.branch_id = branch_id
        self.generation = generation
        self._step_index = 0
        self._prev_absolute: dict[str, Any] | None = None
        self._pending_steps: list[dict[str, Any]] = []
        self._flow_cursor = 0
        self._metrics = None
        self._sim = None

    def bind_sim(self, sim: PlantSimulation) -> None:
        self._sim = sim
        self.bind_metrics(sim.metrics)

    def bind_metrics(self, metrics) -> None:
        self._metrics = metrics
        self._flow_cursor = 0

    def set_fork_state(self, fork_index: int, snapshot: dict[str, Any]) -> None:
        self._step_index = fork_index + 1
        self._prev_absolute = copy.deepcopy(snapshot)
        self._pending_steps = []

    def record_sample(self, sim_minutes: float, snapshot: dict[str, Any]) -> None:
        step_i = self._step_index
        if self._prev_absolute is None:
            mode = "absolute"
            payload = copy.deepcopy(snapshot)
        else:
            mode = "delta"
            payload = diff_snapshot(self._prev_absolute, snapshot)
        self._prev_absolute = copy.deepcopy(snapshot)
        self._pending_steps.append(
            {
                "i": step_i,
                "t": round(sim_minutes, 2),
                "mode": mode,
                "snapshot": payload,
            }
        )
        if self.on_checkpoint and self._sim is not None:
            cp = capture_checkpoint(self._sim, step_i)
            self.on_checkpoint(step_i, cp.to_dict())
        self._step_index += 1
        if len(self._pending_steps) >= self.batch_size:
            self.flush_batch()

    def _drain_flow_events(self) -> list[dict]:
        if self._metrics is None:
            return []
        events = self._metrics.flow_events
        chunk = events[self._flow_cursor :]
        self._flow_cursor = len(events)
        return list(chunk)

    def flush_batch(self, *, force: bool = False) -> None:
        if not self._pending_steps and not force:
            return
        if not self._pending_steps:
            return
        step_from = self._pending_steps[0]["i"]
        step_to = self._pending_steps[-1]["i"]
        batch = SimBatch(
            branch_id=self.branch_id,
            generation=self.generation,
            step_from=step_from,
            step_to=step_to,
            steps=self._pending_steps,
            flow_events=self._drain_flow_events(),
        )
        self.on_batch(batch.to_dict())
        self._pending_steps = []


def run_simulation_streaming(
    config: PlantConfig,
    project_root: Path | None,
    *,
    seed: int | None = 42,
    sample_interval_minutes: float = 1.0,
    on_batch: Callable[[dict[str, Any]], None],
    batch_size: int = 20,
    viz_mode: bool = True,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_checkpoint: Callable[[int, dict[str, Any]], None] | None = None,
    initial_checkpoint: Checkpoint | dict[str, Any] | None = None,
    fork_index: int = -1,
    branch_id: int = 0,
    generation: int = 0,
) -> SimulationResult:
    root = project_root or Path(__file__).resolve().parents[2]
    sim = PlantSimulation(
        config,
        root,
        seed=seed,
        track_flow=False,
        sample_interval_minutes=sample_interval_minutes,
        record_flow_events=True,
    )
    recorder = StreamRecorder(
        on_batch,
        batch_size=batch_size,
        branch_id=branch_id,
        generation=generation,
        on_checkpoint=on_checkpoint,
    )
    recorder.bind_sim(sim)

    cp: Checkpoint | None = None
    if initial_checkpoint is not None:
        cp = (
            initial_checkpoint
            if isinstance(initial_checkpoint, Checkpoint)
            else Checkpoint.from_dict(initial_checkpoint)
        )
        apply_checkpoint(sim, cp)
        recorder.set_fork_state(fork_index, cp.snapshot)

    sim.attach_stream_recorder(recorder, cancel_check=cancel_check)
    try:
        result = sim.run(
            progress_callback=progress_callback,
            drain_sim_days=1.0 if viz_mode else None,
        )
    except SimulationCancelled:
        result = SimulationResult(
            config=config,
            metrics=sim.metrics,
            summary=sim.metrics.to_dict(),
            flow=sim.flow,
        )
    recorder.flush_batch(force=True)
    return result


def batches_to_samples(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct full sample list from streamed batches (client/server assembly)."""
    from plant_sim.snapshot_delta import decode_step

    samples: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for batch in batches:
        for step in batch.get("steps") or []:
            abs_snap = decode_step(prev, step)
            prev = abs_snap
            samples.append({"t": step.get("t", 0.0), **abs_snap})
    return samples
