"""Explicit plant zones: waiting areas, bins, backlog, queue→service workers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import simpy

from plant_sim.config_models import PlantConfig, StageShift, WasherResourceDef
from plant_sim.policies import WasherPoolState

if TYPE_CHECKING:
    from plant_sim.metrics import MetricsCollector

DEFAULT_BIN_FILL_MINUTES = 5.0


@dataclass
class PlantZoneState:
    """Live zone depths for snapshots and visualization."""

    trucks_arrived: int = 0
    pre_scan_waiting: int = 0
    post_scan_waiting: int = 0
    separation_backlog: int = 0

    def snapshot_scan_workers(self, stations: list[ScanStation]) -> dict[str, dict]:
        return {
            s.station_id: {
                "wait": len(s.wait_fifo),
                "busy": s.service.count >= s.service.capacity,
            }
            for s in stations
        }

    def snapshot_washers(self, lines: list[WasherBinLine]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for ln in lines:
            in_bin = ln.bin_fill
            cap = ln.wdef.capacity_items
            out[ln.washer_id] = {
                "bin_fill": in_bin,
                "bin_capacity": cap,
                "pending_to_bin": 0,
                "in_cycle": ln.in_cycle,
                "cycle_progress": round(ln.cycle_progress, 3),
                "batch_size": ln.batch_size,
                "queue_depth": in_bin + (ln.batch_size if ln.in_cycle else 0),
            }
        return out

    def snapshot_workers(self, stations: list[WorkerStation]) -> dict[str, dict]:
        return {
            s.station_id: {
                "wait": len(s.wait_fifo),
                "busy": s.service.count >= s.service.capacity,
                "queue_depth": len(s.wait_fifo) + s.service.count,
            }
            for s in stations
        }


@dataclass
class ScanStation:
    """Scan worker slot: small wait queue → service box."""

    station_id: str
    index: int
    env: simpy.Environment
    service: simpy.Resource
    wait_fifo: deque[int] = field(default_factory=deque)
    _waiters: dict[int, simpy.Event] = field(default_factory=dict)
    log_event: Callable[..., None] | None = None
    zones: PlantZoneState | None = None
    metrics: MetricsCollector | None = None
    service_minutes: float = 1.0

    def __post_init__(self) -> None:
        self.env.process(self._operator())

    def submit(self, item_id: int) -> simpy.Event:
        done = self.env.event()
        self.wait_fifo.append(item_id)
        self._waiters[item_id] = done
        if self.log_event:
            self.log_event(
                self.env.now,
                "move",
                fr="pre_scan_waiting",
                to=self.station_id,
                n=1,
            )
        return done

    def _operator(self) -> simpy.events.Generator:
        while True:
            if not self.wait_fifo:
                yield self.env.timeout(0.2)
                continue
            item_id = self.wait_fifo.popleft()
            req_start = self.env.now
            with self.service.request() as req:
                yield req
                yield self.env.timeout(self.service_minutes)
            done = self._waiters.pop(item_id, None)
            if done and not done.triggered:
                done.succeed()
            if self.metrics:
                self.metrics.record_stage(
                    "scan_in",
                    1,
                    "scan_in",
                    self.service_minutes,
                    count=1,
                )
            if self.log_event:
                self.log_event(
                    self.env.now,
                    "move",
                    fr=self.station_id,
                    to="post_scan_waiting",
                    n=1,
                )
            if self.zones:
                self.zones.post_scan_waiting += 1


@dataclass
class WorkerStation:
    """Press / spotter / jacket: wait queue → press area (service) → done."""

    station_id: str
    stage: str
    index: int
    env: simpy.Environment
    service: simpy.Resource
    service_minutes: float
    wait_fifo: deque[int] = field(default_factory=deque)
    _waiters: dict[int, simpy.Event] = field(default_factory=dict)
    metrics: MetricsCollector | None = None
    log_event: Callable[..., None] | None = None
    shift: StageShift | None = None
    breaks: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.env.process(self._operator())

    def _yield_calendar_wait(self) -> simpy.events.Generator:
        from plant_sim.time_utils import calendar_wait_minutes, clock_from_sim

        while True:
            _, within = clock_from_sim(self.env.now)
            wait_min = calendar_wait_minutes(within, self.shift, self.breaks)
            if wait_min <= 0.01:
                break
            yield self.env.timeout(wait_min)

    @property
    def wait_depth(self) -> int:
        return len(self.wait_fifo)

    def _downstream_node(self) -> str:
        if self.stage == "spotting":
            return "press_conveyor"
        if self.stage == "general_press":
            return "final_qc"
        return self.stage

    def submit(self, item_id: int, from_node: str) -> simpy.Event:
        done = self.env.event()
        self.wait_fifo.append(item_id)
        self._waiters[item_id] = done
        if self.log_event:
            self.log_event(
                self.env.now, "move", fr=from_node, to=f"{self.station_id}:wait", n=1
            )
        return done

    def _operator(self) -> simpy.events.Generator:
        while True:
            if not self.wait_fifo:
                yield self.env.timeout(0.2)
                continue
            yield from self._yield_calendar_wait()
            if not self.wait_fifo:
                continue
            item_id = self.wait_fifo.popleft()
            if self.log_event:
                self.log_event(
                    self.env.now,
                    "move",
                    fr=f"{self.station_id}:wait",
                    to=f"{self.station_id}:press",
                    n=1,
                )
            yield from self._yield_calendar_wait()
            req_start = self.env.now
            with self.service.request() as req:
                yield req
                yield self.env.timeout(self.service_minutes)
            wait_min = self.env.now - req_start
            if self.metrics:
                self.metrics.record_unit_stage(
                    self.stage, self.index, self.service_minutes, wait_minutes=wait_min
                )
                self.metrics.record_stage(
                    self.stage,
                    1,
                    self.stage,
                    self.service_minutes,
                    wait_minutes=wait_min,
                    count=1,
                )
            if self.log_event:
                self.log_event(
                    self.env.now,
                    "move",
                    fr=f"{self.station_id}:press",
                    to=self._downstream_node(),
                    n=1,
                )
            done = self._waiters.pop(item_id, None)
            if done and not done.triggered:
                done.succeed()


@dataclass
class WasherBinLine:
    """Basket in front of washer; full batch cycle; releases to separation backlog."""

    washer_id: str
    wdef: WasherResourceDef
    env: simpy.Environment
    config: PlantConfig
    metrics: MetricsCollector
    zones: PlantZoneState
    log_event: Callable[..., None]
    bin_items: list[tuple[int, simpy.Event]] = field(default_factory=list)
    in_cycle: bool = False
    batch_size: int = 0
    cycle_started_at: float = 0.0
    _pending: list[tuple[int, simpy.Event]] = field(default_factory=list)
    _sibling_lines: list[WasherBinLine] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.env.process(self._operator())

    def _plant_pending_count(self) -> int:
        lines = self._sibling_lines or [self]
        return sum(len(ln._pending) for ln in lines)

    def _drain_all_pending_into_bin(self) -> None:
        while self._pending and len(self.bin_items) < self.wdef.capacity_items:
            self._pull_pending_into_bin()

    def _ready_for_partial_batch(self) -> bool:
        """Start a non-full batch only when no reserve remains plant-wide."""
        policy = self.config.policies.wash_batching
        if not policy.allow_partial_load or not self.bin_items:
            return False
        if self._pending or self.zones.post_scan_waiting > 0:
            return False
        if self._plant_pending_count() > 0:
            return False
        return True

    @property
    def bin_fill(self) -> int:
        return len(self.bin_items)

    @property
    def cycle_progress(self) -> float:
        if not self.in_cycle or self.wdef.cycle_minutes <= 0:
            return 0.0
        elapsed = self.env.now - self.cycle_started_at
        return min(1.0, elapsed / self.wdef.cycle_minutes)

    @property
    def queue_depth(self) -> int:
        return self.bin_fill + len(self._pending) + (
            self.batch_size if self.in_cycle else 0
        )

    def spare_capacity(self) -> int:
        """Items that can still enter this bin (not in cycle)."""
        if self.in_cycle:
            return 0
        return max(
            0,
            self.wdef.capacity_items - self.bin_fill - len(self._pending),
        )

    def enqueue_to_bin(self, item_id: int) -> simpy.Event:
        if self.spare_capacity() <= 0:
            raise RuntimeError(
                f"{self.washer_id}: bin at capacity "
                f"({self.bin_fill}+{len(self._pending)}/{self.wdef.capacity_items})"
            )
        done = self.env.event()
        if self._fill_minutes() <= 0 and self.bin_fill < self.wdef.capacity_items:
            self.bin_items.append((item_id, done))
            self.log_event(
                self.env.now,
                "move",
                fr="post_scan_waiting",
                to=f"{self.washer_id}:bin",
                n=1,
            )
        else:
            self._pending.append((item_id, done))
        self.metrics.record_washer_queue(self.washer_id, self.queue_depth)
        return done

    def _fill_minutes(self) -> float:
        if self.config.policies.wash_batching.start_when_full_or_idle:
            return DEFAULT_BIN_FILL_MINUTES
        return 0.0

    def _pull_pending_into_bin(self) -> None:
        if not self._pending or len(self.bin_items) >= self.wdef.capacity_items:
            return
        item_id, done = self._pending.pop(0)
        self.bin_items.append((item_id, done))
        self.log_event(
            self.env.now,
            "move",
            fr="post_scan_waiting",
            to=f"{self.washer_id}:bin",
            n=1,
        )

    def _spread_fill_into_bin(self) -> simpy.events.Generator:
        """Move pending items into the basket evenly across the fill window."""
        fill_duration = self._fill_minutes()
        if fill_duration <= 0:
            while self._pending and len(self.bin_items) < self.wdef.capacity_items:
                self._pull_pending_into_bin()
            return

        fill_end = self.env.now + fill_duration
        while (
            self.env.now < fill_end
            and self._pending
            and len(self.bin_items) < self.wdef.capacity_items
        ):
            remaining_time = fill_end - self.env.now
            slots = self.wdef.capacity_items - len(self.bin_items)
            n = min(len(self._pending), slots)
            if n <= 0 or remaining_time <= 0:
                break
            self._pull_pending_into_bin()
            n -= 1
            if n <= 0:
                continue
            yield self.env.timeout(remaining_time / n)

    def _operator(self) -> simpy.events.Generator:
        cap = self.wdef.capacity_items
        while True:
            while not self._pending and not self.bin_items:
                yield self.env.timeout(0.25)

            if self._pending and len(self.bin_items) < cap:
                yield from self._spread_fill_into_bin()

            self._drain_all_pending_into_bin()

            if self._pending and not self.bin_items:
                self._pull_pending_into_bin()

            if not self.bin_items:
                yield self.env.timeout(0.25)
                continue

            policy = self.config.policies.wash_batching
            if len(self.bin_items) < cap:
                if not policy.allow_partial_load:
                    while len(self.bin_items) < cap:
                        if not self._pending:
                            if self.zones.post_scan_waiting > 0:
                                yield self.env.timeout(0.5)
                                break
                            yield self.env.timeout(0.25)
                            break
                        self._pull_pending_into_bin()
                    if len(self.bin_items) < cap:
                        continue
                elif not self._ready_for_partial_batch():
                    yield self.env.timeout(0.25)
                    continue

            batch = self.bin_items[:]
            self.bin_items = []
            self.in_cycle = True
            self.batch_size = len(batch)
            self.cycle_started_at = self.env.now
            self.log_event(
                self.env.now,
                "wash_batch_start",
                washer=self.washer_id,
                count=self.batch_size,
            )
            self.metrics.record_washer_queue(self.washer_id, self.queue_depth)

            yield self.env.timeout(self.wdef.cycle_minutes)

            per_item = self.wdef.cycle_minutes / max(self.batch_size, 1)
            released: list[int] = []
            for item_id, done in batch:
                self.metrics.record_stage(
                    "wash", 1, "wash", per_item, wait_minutes=0.0, count=1
                )
                released.append(item_id)
                if not done.triggered:
                    done.succeed()

            self.log_event(
                self.env.now,
                "wash_batch_end",
                washer=self.washer_id,
                count=self.batch_size,
            )

            self.in_cycle = False
            self.batch_size = 0
            self.metrics.record_washer_queue(self.washer_id, self.queue_depth)


def pick_fill_first_washer(
    lines: list[WasherBinLine],
    pool: WasherPoolState | None = None,
) -> WasherBinLine | None:
    """Fill the fullest bin that still has space; else round-robin among empty drums."""

    pool = pool or WasherPoolState()
    eligible = [ln for ln in lines if ln.spare_capacity() > 0]
    if not eligible:
        return None

    filling = [ln for ln in eligible if ln.bin_fill > 0]
    if filling:
        return max(filling, key=lambda ln: (ln.bin_fill, ln.wdef.capacity_items))

    empty = [ln for ln in eligible if ln.bin_fill == 0 and not ln._pending]
    if not empty:
        return min(eligible, key=lambda ln: (ln.bin_fill + len(ln._pending), ln.washer_id))

    ranked = sorted(
        empty,
        key=lambda ln: (-ln.wdef.capacity_items, ln.washer_id),
    )
    idx = pool.dispatch_index % len(ranked)
    pool.dispatch_index = (pool.dispatch_index + 1) % len(ranked)
    return ranked[idx]


# Backward-compatible alias
pick_shortest_washer = pick_fill_first_washer


def pick_shortest_worker(stations: list[WorkerStation]) -> WorkerStation:
    return min(stations, key=lambda s: len(s.wait_fifo) + s.service.count)
