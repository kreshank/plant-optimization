"""Generic SimPy DES engine — all behavior from PlantConfig."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import simpy

from plant_sim.config_models import PlantConfig, WasherResourceDef
from plant_sim.flow_tracker import FlowTracker
from plant_sim.metrics import MetricsCollector
from plant_sim.policies import (
    WasherPoolState,
    effective_loss_rate,
    evaluate_staffing_mode,
    ramp_factor,
)
from plant_sim.schedule import TruckWave, load_truck_schedule
from plant_sim.time_utils import (
    WEEKDAY_MAP,
    break_intervals_minutes,
    clock_from_sim,
    day_open_minutes,
    delay_for_shift,
    is_operating_day,
    minutes_until_break_ends,
    wash_cutoff_minutes,
)

DAY_NAMES = [k for k, v in sorted(WEEKDAY_MAP.items(), key=lambda x: x[1])]

WORKER_STAGES = (
    "scan_in",
    "separation",
    "steam_tunnel",
    "jacket_press",
    "general_press",
    "final_qc",
    "spotting",
    "delivery_scan",
)


@dataclass
class SimulationResult:
    config: PlantConfig
    metrics: MetricsCollector
    summary: dict
    flow: FlowTracker | None = None

    @property
    def items_completed(self) -> int:
        return self.metrics.items_completed


def _stage_labor_role(stage_name: str, stage_cfg) -> str | None:
    if stage_cfg.labor_role:
        return stage_cfg.labor_role
    return stage_name


class PlantSimulation:
    def __init__(
        self,
        config: PlantConfig,
        project_root: Path,
        seed: int | None = None,
        track_flow: bool = False,
    ):
        self.config = config
        self.root = project_root
        self.rng = random.Random(seed)
        self.env = simpy.Environment()
        self.metrics = MetricsCollector(config=config)
        self._scan_in_enabled = config.stages.scan_in.enabled
        self.flow = (
            FlowTracker(scan_in_enabled=self._scan_in_enabled) if track_flow else None
        )
        self._sim_duration_minutes = 0.0
        self._wip_counter = 0
        self._breaks = break_intervals_minutes(config.calendar)
        self.washer_state = WasherPoolState()
        for w in config.resources.washers:
            self.washer_state.register_type(w.id, w.count)

        self._washer_units: list[tuple[WasherResourceDef, simpy.Resource]] = []
        for wdef in config.resources.washers:
            for _ in range(wdef.count):
                self._washer_units.append(
                    (wdef, simpy.Resource(self.env, capacity=1))
                )

        self._stage_resources: dict[str, simpy.Resource] = {}
        self._scan_resources: dict[str, simpy.Resource] = {}
        self._init_stage_resources()

        self.truck_waves = load_truck_schedule(config, project_root)
        self._schedule_by_weekday: dict[str, list[TruckWave]] = {}
        for wave in self.truck_waves:
            self._schedule_by_weekday.setdefault(wave.day_of_week, []).append(wave)

    def _init_stage_resources(self) -> None:
        stages = self.config.stages
        scan = stages.scan_in
        if self._scan_in_enabled and scan.workers:
            if hasattr(scan.workers, "normal") and scan.workers.normal:
                self._scan_resources["normal"] = simpy.Resource(
                    self.env, capacity=max(scan.workers.normal, 1)
                )
            if hasattr(scan.workers, "reduced") and scan.workers.reduced:
                self._scan_resources["reduced"] = simpy.Resource(
                    self.env, capacity=max(scan.workers.reduced, 1)
                )
        if self._scan_in_enabled and not self._scan_resources:
            n = max(scan.worker_count(), 1)
            self._scan_resources["normal"] = simpy.Resource(self.env, capacity=n)

        for name in WORKER_STAGES:
            if name == "scan_in":
                continue
            stage = getattr(stages, name)
            if not stage.enabled:
                continue
            cap = max(stage.worker_count(), 1)
            self._stage_resources[name] = simpy.Resource(self.env, capacity=cap)

    def _update_washer_busy_flags(self) -> None:
        busy: dict[str, int] = {
            wid: 0 for wid in self.config.all_washer_resource_ids()
        }
        for wdef, res in self._washer_units:
            if res.count >= res.capacity:
                busy[wdef.id] = busy.get(wdef.id, 0) + 1
        for wid, c in busy.items():
            self.washer_state.set_busy(wid, c)

    def _wash_minutes_per_item(self, wdef: WasherResourceDef) -> float:
        policy = self.config.policies.wash_batching
        if policy.start_when_full_or_idle:
            return wdef.cycle_minutes / wdef.capacity_items
        return wdef.cycle_minutes

    def _per_item_service_minutes(
        self,
        stage_name: str,
        worker_mode: str = "normal",
    ) -> float:
        stage = getattr(self.config.stages, stage_name)
        scan = self.config.scan_seconds_per_item
        day_idx = int(self.env.now // (24 * 60))
        ramp = ramp_factor(self.config, day_idx)
        if stage.requires_scan:
            effective_scan = scan * (1 - ramp * self.config.loss_model.coverage)
        else:
            effective_scan = 0.0
        seconds = stage.service_seconds(effective_scan)
        return seconds / 60.0

    def _record_queue(self, resource: simpy.Resource, stage_id: str) -> None:
        waiting = len(resource.queue)
        in_service = resource.count
        self.metrics.record_queue_depth(stage_id, waiting + in_service)

    def _yield_calendar_wait(self, stage_name: str):
        while True:
            _, within = clock_from_sim(self.env.now)
            stage = getattr(self.config.stages, stage_name)
            break_wait = minutes_until_break_ends(within, self._breaks)
            shift_wait = delay_for_shift(within, stage.shift)
            wait_min = max(break_wait, shift_wait)
            if wait_min <= 0.01:
                break
            yield self.env.timeout(wait_min)

    def _transfer_delay(self, transfer_key: str):
        minutes = getattr(self.config.transfers, transfer_key, 0.0) or 0.0
        if minutes > 0:
            yield self.env.timeout(minutes)

    def _run_stage(
        self,
        item_id: int,
        stage_name: str,
        worker_mode: str = "normal",
    ):
        stage = getattr(self.config.stages, stage_name)
        if not stage.enabled:
            return

        yield from self._yield_calendar_wait(stage_name)

        if self.flow:
            self.flow.stage_start(item_id, stage_name, self.env.now)

        workers = stage.worker_count(worker_mode)
        role = _stage_labor_role(stage_name, stage)

        if stage_name == "scan_in":
            pool = self._scan_resources.get(
                worker_mode, self._scan_resources.get("normal")
            )
        else:
            pool = self._stage_resources[stage_name]

        service_min = self._per_item_service_minutes(stage_name, worker_mode)
        req_start = self.env.now

        with pool.request() as req:
            self._record_queue(pool, stage_name)
            yield req
            wait_min = self.env.now - req_start
            yield self.env.timeout(service_min)

        self.metrics.record_stage(
            stage_name,
            workers,
            role,
            service_min,
            wait_minutes=wait_min,
            count=1,
        )

        if self.flow:
            self.flow.stage_complete(item_id, stage_name, self.env.now)

    def _steam_post_check(self, item_id: int):
        stage = self.config.stages.steam_tunnel
        post = stage.post_check_seconds
        if not post or post <= 0:
            return
        yield from self._yield_calendar_wait("steam_tunnel")
        check_min = post / 60.0
        yield self.env.timeout(check_min)
        self.metrics.record_stage(
            "steam_exit_check",
            1,
            stage.labor_role or "steam",
            check_min,
            count=1,
        )

    def _route_after_separation(self) -> str:
        r = self.config.routing.after_separation
        roll = self.rng.uniform(0, 100)
        if roll < r.pct_spotting:
            return "spotting"
        roll -= r.pct_spotting
        if roll < r.pct_steam_tunnel:
            return "steam_tunnel"
        roll -= r.pct_steam_tunnel
        if roll < r.pct_jacket_press:
            return "jacket_press"
        return "general_press"

    def _route_after_steam(self) -> str:
        r = self.config.routing.after_steam
        if self.rng.uniform(0, 100) < r.pct_needs_press:
            return "general_press"
        return "final_qc"

    def _wash_item(self, item_id: int):
        if self.flow:
            self.flow.stage_start(item_id, "wash", self.env.now)

        open_min = day_open_minutes(self.config.calendar)
        cutoff = wash_cutoff_minutes(self.config.calendar)
        _, within = divmod(self.env.now, 24 * 60)

        if within >= cutoff or within < open_min:
            self.metrics.items_deferred_wash += 1
            day_base = int(self.env.now // (24 * 60))
            if within >= cutoff:
                wait_until = (day_base + 1) * 24 * 60 + open_min
            else:
                wait_until = day_base * 24 * 60 + open_min
            delay = max(0.0, wait_until - self.env.now)
            if delay > 0:
                yield self.env.timeout(delay)

        wdef, resource = self.rng.choice(self._washer_units)
        wash_duration = self._wash_minutes_per_item(wdef)
        req_start = self.env.now
        with resource.request() as req:
            self._update_washer_busy_flags()
            self._record_queue(resource, "wash")
            yield req
            self._update_washer_busy_flags()
            wait_min = self.env.now - req_start
            yield self.env.timeout(wash_duration)
            self.metrics.record_stage(
                "wash",
                1,
                "wash",
                wash_duration,
                wait_minutes=wait_min,
                count=1,
            )
        self._update_washer_busy_flags()

        if self.flow:
            self.flow.stage_complete(item_id, "wash", self.env.now)

    def _qc_loop(self, item_id: int, cycle: int):
        policy = self.config.policies.qc_rework
        yield from self._run_stage(item_id, "final_qc")
        defect = self.config.stages.final_qc.defect_rate or 0.0
        if self.rng.random() < defect and cycle < policy.max_cycles:
            self.metrics.qc_rework_cycles += 1
            if self.flow:
                self.flow.mark_rework(item_id)
                self.flow.set_qc_path(item_id, "rework_path")
            yield from self._transfer_delay("after_spotting")
            yield from self._run_stage(item_id, "spotting")
            yield from self._transfer_delay("after_general_press")
            yield from self._run_stage(item_id, "general_press")
            yield from self._qc_loop(item_id, cycle + 1)
            return

        if self.config.stages.delivery_scan.enabled:
            yield from self._run_stage(item_id, "delivery_scan")
            self.metrics.record_delivery_ready(
                self.env.now,
                day_open_minutes(self.config.calendar),
            )
        self.metrics.items_completed += 1

    def _wip_spotting_pipeline(self, item_id: int):
        if self.flow:
            for stage in ("scan_in", "wash", "separation"):
                self.flow.stage_complete(item_id, stage, self.env.now)
            self.flow.set_qc_path(item_id, "spotting_path")
        yield from self._run_stage(item_id, "spotting")
        yield from self._transfer_delay("after_spotting")
        yield from self._run_stage(item_id, "general_press")
        yield from self._qc_loop(item_id, cycle=0)

    def _item_pipeline(self, item_id: int):
        loss_rate = effective_loss_rate(self.config)
        if self.rng.random() < loss_rate:
            self.metrics.items_lost += 1
            return

        if self._scan_in_enabled:
            scan_mode = evaluate_staffing_mode(self.config, self.washer_state)
            yield from self._run_stage(item_id, "scan_in", worker_mode=scan_mode)
        elif self.flow:
            self.flow.stage_complete(item_id, "scan_in", self.env.now)

        yield from self._wash_item(item_id)
        yield from self._transfer_delay("after_wash")
        yield from self._run_stage(item_id, "separation")
        yield from self._transfer_delay("after_separation")

        path = self._route_after_separation()
        if path == "spotting":
            if self.flow:
                self.flow.set_qc_path(item_id, "spotting_path")
            yield from self._run_stage(item_id, "spotting")
            yield from self._transfer_delay("after_spotting")
            yield from self._run_stage(item_id, "general_press")
            yield from self._transfer_delay("after_general_press")
        elif path == "steam_tunnel":
            yield from self._run_stage(item_id, "steam_tunnel")
            yield from self._transfer_delay("after_steam_tunnel")
            yield from self._steam_post_check(item_id)
            if self._route_after_steam() == "general_press":
                if self.flow:
                    self.flow.set_qc_path(item_id, "steam_press_path")
                yield from self._run_stage(item_id, "general_press")
                yield from self._transfer_delay("after_general_press")
            elif self.flow:
                self.flow.set_qc_path(item_id, "steam_direct_path")
        elif path == "jacket_press":
            if self.flow:
                self.flow.set_qc_path(item_id, "jacket_path")
            yield from self._run_stage(item_id, "jacket_press")
            yield from self._transfer_delay("after_jacket_press")
        else:
            if self.flow:
                self.flow.set_qc_path(item_id, "press_path")
            yield from self._run_stage(item_id, "general_press")
            yield from self._transfer_delay("after_general_press")

        yield from self._qc_loop(item_id, cycle=0)

    def _inject_items(self, count: float) -> None:
        n = int(count)
        remainder = count - n
        self.metrics.items_injected += count
        for _ in range(n):
            item_id = self._wip_counter
            self._wip_counter += 1
            self.env.process(self._item_pipeline(item_id))
        if remainder > 0.01 and self.rng.random() < remainder:
            item_id = self._wip_counter
            self._wip_counter += 1
            self.env.process(self._item_pipeline(item_id))

    def _seed_wip(self) -> None:
        spotting_wip = self.config.wip.initial_by_stage.get("spotting", 0)
        for _ in range(spotting_wip):
            item_id = self._wip_counter
            self._wip_counter += 1
            self.metrics.items_injected += 1
            self.env.process(self._wip_spotting_pipeline(item_id))

    def _truck_arrival(self, wave: TruckWave, day_index: int):
        open_min = day_open_minutes(self.config.calendar)
        arrival_sim = day_index * 24 * 60 + max(wave.arrival_minutes, open_min)
        delay = max(0.0, arrival_sim - self.env.now)
        yield self.env.timeout(delay)
        if wave.direction == "incoming":
            self._inject_items(wave.total_items(self.config.items_per_truck))

    def run(self) -> SimulationResult:
        cal = self.config.calendar
        days_to_sim = self.config.objectives.simulation_days
        operating_count = 0
        calendar_day = 0
        weekday = 0
        wip_seeded = False

        while operating_count < days_to_sim:
            if is_operating_day(weekday, cal):
                if not wip_seeded:
                    self._seed_wip()
                    wip_seeded = True
                day_name = DAY_NAMES[weekday]
                for wave in self._schedule_by_weekday.get(day_name, []):
                    self.env.process(self._truck_arrival(wave, calendar_day))
                operating_count += 1

            calendar_day += 1
            end = calendar_day * 24 * 60
            if end > self.env.now:
                self.env.run(until=end)
            weekday = (weekday + 1) % 7

        if self.env.now < calendar_day * 24 * 60:
            self.env.run(until=calendar_day * 24 * 60)

        drain_until = self.env.now + 24 * 60 * 16
        self.env.run(until=drain_until)

        self._sim_duration_minutes = self.env.now
        self.metrics.finalize_utilization(self._sim_duration_minutes)

        summary = self.metrics.to_dict()
        target = self.config.objectives.daily_items_target
        if target:
            summary["daily_items_target"] = target
            summary["daily_items_gap"] = round(
                self.metrics.items_completed - target, 1
            )
        if self.flow and not self.flow.ok:
            summary["flow_violations"] = [
                {
                    "item_id": v.item_id,
                    "stage": v.stage,
                    "message": v.message,
                    "sim_time": v.sim_time,
                }
                for v in self.flow.violations[:50]
            ]
        return SimulationResult(
            config=self.config,
            metrics=self.metrics,
            summary=summary,
            flow=self.flow,
        )


def run_simulation(
    config: PlantConfig,
    project_root: Path | None = None,
    seed: int | None = 42,
    track_flow: bool = False,
) -> SimulationResult:
    root = project_root or Path(__file__).resolve().parents[2]
    sim = PlantSimulation(config, root, seed=seed, track_flow=track_flow)
    return sim.run()
