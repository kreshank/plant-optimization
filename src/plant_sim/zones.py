"""Explicit plant zones: waiting areas, bins, backlog, queue→service workers."""

from __future__ import annotations

import math
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
    post_scan_fifo: deque[int] = field(default_factory=deque)
    wash_release_waiters: dict[int, simpy.Event] = field(default_factory=dict)
    item_washer_line: dict[int, str] = field(default_factory=dict)
    """Only this washer may pull from post_scan_fifo until it starts a cycle."""
    active_washer_id: str | None = None
    """Last drum that started a wash cycle (sequential empty-drum selection)."""
    last_cycle_washer_id: str | None = None
    separation_backlog: int = 0
    completed_goods_buffer: deque[int] = field(default_factory=deque)
    completed_waiting: int = 0

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
                    to="scan_out",
                    n=1,
                )


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
    """Basket in front of washer; pulls from post_scan backlog; batch cycle."""

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
    _sibling_lines: list[WasherBinLine] | None = field(default=None, repr=False)
    _washer_pool: WasherPoolState | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.env.process(self._operator())

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
        return self.bin_fill + (self.batch_size if self.in_cycle else 0)

    def spare_capacity(self) -> int:
        if self.in_cycle:
            return 0
        return max(0, self.wdef.capacity_items - self.bin_fill)

    def _min_batch_items(self) -> int:
        cap = self.wdef.capacity_items
        ratio = self.config.policies.wash_batching.min_fill_ratio
        return max(1, math.ceil(cap * ratio))

    def _fill_minutes(self) -> float:
        if self.config.policies.wash_batching.start_when_full_or_idle:
            return DEFAULT_BIN_FILL_MINUTES
        return 0.0

    def _all_lines(self) -> list[WasherBinLine]:
        return self._sibling_lines or [self]

    def _sync_active_filler(self) -> None:
        lines = self._all_lines()
        pool = self._washer_pool or WasherPoolState()
        active_id = self.zones.active_washer_id
        if active_id:
            active_ln = next((ln for ln in lines if ln.washer_id == active_id), None)
            if active_ln is None or active_ln.in_cycle:
                self.zones.active_washer_id = None
        if self.zones.active_washer_id is None:
            chosen = choose_active_filler(lines, pool)
            if chosen is not None:
                self.zones.active_washer_id = chosen.washer_id

    def _is_active_filler(self) -> bool:
        self._sync_active_filler()
        return self.zones.active_washer_id == self.washer_id

    def _has_full_bin_waiting(self) -> bool:
        cap = self.wdef.capacity_items
        return any(
            not ln.in_cycle and ln.bin_fill >= cap for ln in self._all_lines()
        )

    def _can_start_cycle(self) -> bool:
        cap = self.wdef.capacity_items
        n = self.bin_fill
        if n == 0:
            return False
        if self.zones.post_scan_fifo:
            return n >= cap
        return n >= self._min_batch_items()

    def _should_start_now(self) -> bool:
        ready = [
            ln
            for ln in self._all_lines()
            if not ln.in_cycle and ln._can_start_cycle()
        ]
        if not ready:
            return False
        best = max(ready, key=lambda ln: (ln.bin_fill, ln.wdef.capacity_items))
        return best.washer_id == self.washer_id

    def _pull_one_from_backlog(self) -> bool:
        cap = self.wdef.capacity_items
        if (
            self.in_cycle
            or not self._is_active_filler()
            or len(self.bin_items) >= cap
            or not self.zones.post_scan_fifo
        ):
            return False
        item_id = self.zones.post_scan_fifo.popleft()
        self.zones.post_scan_waiting = max(0, self.zones.post_scan_waiting - 1)
        done = self.zones.wash_release_waiters.pop(item_id)
        self.zones.item_washer_line[item_id] = self.washer_id
        self.bin_items.append((item_id, done))
        self.log_event(
            self.env.now,
            "move",
            fr="post_scan_waiting",
            to=f"{self.washer_id}:bin",
            n=1,
        )
        self.metrics.record_washer_queue(self.washer_id, self.queue_depth)
        return True

    def _spread_pull_from_backlog(self) -> simpy.events.Generator:
        """Pull from wash backlog into basket evenly across the fill window."""
        cap = self.wdef.capacity_items
        fill_duration = self._fill_minutes()
        if fill_duration <= 0:
            while (
                self._is_active_filler()
                and self.zones.post_scan_fifo
                and len(self.bin_items) < cap
            ):
                if not self._pull_one_from_backlog():
                    break
            return

        fill_end = self.env.now + fill_duration
        while (
            self.env.now < fill_end
            and self._is_active_filler()
            and self.zones.post_scan_fifo
            and len(self.bin_items) < cap
        ):
            if not self._pull_one_from_backlog():
                break
            remaining_time = fill_end - self.env.now
            slots = cap - len(self.bin_items)
            n = min(len(self.zones.post_scan_fifo), slots)
            if n <= 0 or remaining_time <= 0:
                break
            yield self.env.timeout(remaining_time / n)

    def _run_cycle(self) -> simpy.events.Generator:
        batch = self.bin_items[:]
        self.bin_items = []
        self.in_cycle = True
        self.batch_size = len(batch)
        self.cycle_started_at = self.env.now
        if self.zones.active_washer_id == self.washer_id:
            self.zones.active_washer_id = None
        self.zones.last_cycle_washer_id = self.washer_id
        self.log_event(
            self.env.now,
            "wash_batch_start",
            washer=self.washer_id,
            count=self.batch_size,
        )
        self.metrics.record_washer_queue(self.washer_id, self.queue_depth)

        yield self.env.timeout(self.wdef.cycle_minutes)

        per_item = self.wdef.cycle_minutes / max(self.batch_size, 1)
        for item_id, done in batch:
            self.metrics.record_stage(
                "wash", 1, "wash", per_item, wait_minutes=0.0, count=1
            )
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

    def _operator(self) -> simpy.events.Generator:
        cap = self.wdef.capacity_items

        while True:
            if self.in_cycle:
                yield self.env.timeout(0.25)
                continue

            if self.bin_fill >= cap and self._should_start_now():
                yield from self._run_cycle()
                continue

            if self._has_full_bin_waiting() and not self._can_start_cycle():
                yield self.env.timeout(0.25)
                continue

            if not self._is_active_filler():
                yield self.env.timeout(0.25)
                continue

            if self.bin_fill < cap and self.zones.post_scan_fifo:
                if self._fill_minutes() > 0:
                    yield from self._spread_pull_from_backlog()
                while (
                    self.bin_fill < cap
                    and self.zones.post_scan_fifo
                    and self._is_active_filler()
                ):
                    if not self._pull_one_from_backlog():
                        break

            if not self.bin_items:
                yield self.env.timeout(0.25)
                continue

            if self._can_start_cycle() and self._should_start_now():
                yield from self._run_cycle()
                continue

            yield self.env.timeout(0.25)


def choose_active_filler(
    lines: list[WasherBinLine],
    pool: WasherPoolState,
) -> WasherBinLine | None:
    """Pick the one drum that may pull from wash backlog (exclusive filler)."""

    if any(
        not ln.in_cycle and ln.bin_fill >= ln.wdef.capacity_items for ln in lines
    ):
        return None

    idle = [
        ln
        for ln in lines
        if not ln.in_cycle and ln.bin_fill < ln.wdef.capacity_items
    ]
    if not idle:
        return None

    partial = [ln for ln in idle if ln.bin_fill > 0]
    if partial:
        return max(partial, key=lambda ln: (ln.bin_fill, ln.wdef.capacity_items))

    empty_idle = [
        ln
        for ln in lines
        if not ln.in_cycle and ln.bin_fill < ln.wdef.capacity_items and ln.bin_fill == 0
    ]
    if not empty_idle:
        return None

    zones = getattr(lines[0], "zones", None) if lines else None
    if zones is not None and zones.last_cycle_washer_id:
        ids = [ln.washer_id for ln in empty_idle]
        last = zones.last_cycle_washer_id
        if last in ids:
            return empty_idle[(ids.index(last) + 1) % len(empty_idle)]
    return empty_idle[0]


def pick_fill_first_washer(
    lines: list[WasherBinLine],
    pool: WasherPoolState | None = None,
) -> WasherBinLine | None:
    """Return the exclusive active filler, or the fullest idle line for tests."""

    pool = pool or WasherPoolState()
    zones = getattr(lines[0], "zones", None) if lines else None
    if zones is not None and zones.active_washer_id:
        active = next(
            (ln for ln in lines if ln.washer_id == zones.active_washer_id),
            None,
        )
        if active is not None and not active.in_cycle:
            return active
    return choose_active_filler(lines, pool)


# Backward-compatible alias
pick_shortest_washer = pick_fill_first_washer


def pick_shortest_worker(stations: list[WorkerStation]) -> WorkerStation:
    return min(stations, key=lambda s: len(s.wait_fifo) + s.service.count)
