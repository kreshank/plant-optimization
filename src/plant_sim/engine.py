"""Generic SimPy DES engine — all behavior from PlantConfig."""

from __future__ import annotations

import random
from collections.abc import Callable
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
from plant_sim.unit_tracking import unit_id
from plant_sim.zones import (
    PlantZoneState,
    ScanStation,
    WasherBinLine,
    WorkerStation,
    pick_shortest_worker,
)
from plant_sim.time_utils import (
    WEEKDAY_MAP,
    break_intervals_minutes,
    clock_from_sim,
    day_open_minutes,
    operating_window_bounds,
    wash_cutoff_minutes,
    delay_for_shift,
    is_operating_day,
    minutes_until_break_ends,
    sim_clock_display,
)

DAY_NAMES = [k for k, v in sorted(WEEKDAY_MAP.items(), key=lambda x: x[1])]

WORKER_STAGES = (
    "steam_tunnel",
    "final_qc",
    "delivery_scan",
    "outbound_scan",
)

ZONE_WORKER_STAGES = ("separation", "spotting", "general_press", "jacket_press")


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
        sample_interval_minutes: float | None = None,
        *,
        record_flow_events: bool = True,
    ):
        self.config = config
        self.root = project_root
        self.sample_interval_minutes = sample_interval_minutes
        self._record_flow_events = record_flow_events
        self.rng = random.Random(seed)
        self.env = simpy.Environment()
        self.metrics = MetricsCollector(config=config)
        self._scan_in_enabled = config.stages.scan_in.enabled
        self.flow = (
            FlowTracker(scan_in_enabled=self._scan_in_enabled) if track_flow else None
        )
        self._sim_duration_minutes = 0.0
        self._wip_counter = 0
        self._start_weekday = 0
        self._breaks = break_intervals_minutes(config.calendar)
        self.washer_state = WasherPoolState()
        for w in config.resources.washers:
            self.washer_state.register_type(w.id, w.count)

        self.zones = PlantZoneState()

        self._washer_lines: list[WasherBinLine] = []
        for wdef in config.resources.washers:
            for i in range(wdef.count):
                wid = f"{wdef.id}:{i}"
                self._washer_lines.append(
                    WasherBinLine(
                        washer_id=wid,
                        wdef=wdef,
                        env=self.env,
                        config=config,
                        metrics=self.metrics,
                        zones=self.zones,
                        log_event=self._log_flow,
                    )
                )
        for ln in self._washer_lines:
            ln._sibling_lines = self._washer_lines
            ln._washer_pool = self.washer_state

        self._scan_stations: list[ScanStation] = []
        if self._scan_in_enabled:
            scan_cfg = config.stages.scan_in
            n_scan = max(scan_cfg.worker_count(), 1)
            scan_min = scan_cfg.service_seconds(0) / 60.0
            for i in range(n_scan):
                self._scan_stations.append(
                    ScanStation(
                        station_id=f"scan_in:{i}",
                        index=i,
                        env=self.env,
                        service=simpy.Resource(self.env, capacity=1),
                        log_event=self._log_flow,
                        zones=self.zones,
                        metrics=self.metrics,
                        service_minutes=scan_min,
                    )
                )

        self._worker_stations: dict[str, list[WorkerStation]] = {}
        for stage_name in ZONE_WORKER_STAGES:
            stage = getattr(config.stages, stage_name)
            if not stage.enabled:
                continue
            stations: list[WorkerStation] = []
            n = max(stage.worker_count(), 1)
            svc_min = stage.service_seconds(0) / 60.0
            for i in range(n):
                stations.append(
                    WorkerStation(
                        station_id=f"{stage_name}:{i}",
                        stage=stage_name,
                        index=i,
                        env=self.env,
                        service=simpy.Resource(self.env, capacity=1),
                        service_minutes=svc_min,
                        metrics=self.metrics,
                        log_event=self._log_flow,
                        shift=stage.shift,
                        breaks=self._breaks,
                    )
                )
                self.metrics.ensure_unit(stage_name, i)
            self._worker_stations[stage_name] = stations

        self._stage_resources: dict[str, simpy.Resource] = {}
        self._init_stage_resources()
        if self.sample_interval_minutes and self.sample_interval_minutes > 0:
            self.metrics.init_time_series(
                self.sample_interval_minutes,
                record_flow_events=self._record_flow_events,
            )

        self.truck_waves = load_truck_schedule(config, project_root)
        self._schedule_by_weekday: dict[str, list[TruckWave]] = {}
        for wave in self.truck_waves:
            self._schedule_by_weekday.setdefault(wave.day_of_week, []).append(wave)

    def _init_stage_resources(self) -> None:
        stages = self.config.stages
        for name in WORKER_STAGES:
            stage = getattr(stages, name)
            if not stage.enabled:
                continue
            cap = max(stage.worker_count(), 1)
            self._stage_resources[name] = simpy.Resource(self.env, capacity=cap)

    def _log_flow(self, sim_minutes: float, kind: str, **fields) -> None:
        self.metrics.log_flow_event(sim_minutes, kind, **fields)

    def _update_washer_busy_flags(self) -> None:
        busy: dict[str, int] = {
            wid: 0 for wid in self.config.all_washer_resource_ids()
        }
        for line in self._washer_lines:
            type_id = line.wdef.id
            if line.in_cycle:
                busy[type_id] = busy.get(type_id, 0) + 1
        for wid, c in busy.items():
            self.washer_state.set_busy(wid, c)

    def _run_worker_station(
        self, item_id: int, stage_name: str, from_node: str
    ):
        stations = self._worker_stations.get(stage_name, [])
        if not stations:
            yield from self._run_stage(item_id, stage_name, from_node=from_node)
            return
        yield from self._yield_calendar_wait(stage_name)
        if self.flow:
            self.flow.stage_start(item_id, stage_name, self.env.now)
        station = pick_shortest_worker(stations)
        yield station.submit(item_id, from_node)
        if self.flow:
            self.flow.stage_complete(item_id, stage_name, self.env.now)

    def _separation_backlog_depth(self) -> int:
        stations = self._worker_stations.get("separation", [])
        return sum(len(st.wait_fifo) + st.service.count for st in stations)

    def _sync_separation_backlog(self) -> None:
        self.zones.separation_backlog = self._separation_backlog_depth()

    def _inbound_backlog_depth(self) -> int:
        total = self.zones.pre_scan_waiting
        for st in self._scan_stations:
            total += len(st.wait_fifo)
        return total

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

    def _capture_queue_snapshot(self) -> dict:
        units: dict[str, dict] = {}
        for stage, stations in self._worker_stations.items():
            for st in stations:
                units[st.station_id] = {
                    "stage": stage,
                    "index": st.index,
                    "waiting": len(st.wait_fifo),
                    "busy": st.service.count >= st.service.capacity,
                    "queue_depth": len(st.wait_fifo) + st.service.count,
                }
        stages: dict[str, dict] = {}
        for stage_name, res in self._stage_resources.items():
            waiting = len(res.queue)
            stages[stage_name] = {
                "waiting": waiting,
                "queue_depth": waiting + res.count,
            }
        washers = self.zones.snapshot_washers(self._washer_lines)
        zone_state = {
            "trucks_arrived": self.zones.trucks_arrived,
            "pre_scan_waiting": self.zones.pre_scan_waiting,
            "post_scan_waiting": self.zones.post_scan_waiting,
            "inbound_backlog": self._inbound_backlog_depth(),
            "separation_backlog": self._separation_backlog_depth(),
            "scan_workers": self.zones.snapshot_scan_workers(self._scan_stations),
        }
        return {
            "units": units,
            "stages": stages,
            "washers": washers,
            "zones": zone_state,
            "items_completed": self.metrics.items_completed,
            "clock": sim_clock_display(
                self.env.now, self.config.calendar, self._start_weekday
            ),
        }

    def _queue_sampler(self):
        interval = self.sample_interval_minutes or 0.0
        while True:
            yield self.env.timeout(interval)
            day_idx, within = divmod(self.env.now, 24 * 60)
            weekday = (self._start_weekday + int(day_idx)) % 7
            if not is_operating_day(weekday, self.config.calendar):
                continue
            cal = self.config.calendar
            if within < day_open_minutes(cal):
                continue
            if within > wash_cutoff_minutes(cal):
                continue
            self.metrics.append_time_series_sample(
                self.env.now, self._capture_queue_snapshot()
            )

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
        from_node: str | None = None,
    ):
        stage = getattr(self.config.stages, stage_name)
        if not stage.enabled:
            return

        yield from self._yield_calendar_wait(stage_name)

        if self.flow:
            self.flow.stage_start(item_id, stage_name, self.env.now)

        workers = stage.worker_count(worker_mode)
        role = _stage_labor_role(stage_name, stage)
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
        if from_node:
            self._log_flow(
                self.env.now, "move", fr=from_node, to=stage_name, n=1
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

    def _wait_through_wash(self, item_id: int, *, from_node: str):
        """Join wash backlog; release after primary drum completes a batch."""
        done = self.env.event()
        self.zones.wash_release_waiters[item_id] = done
        self.zones.post_scan_fifo.append(item_id)
        self.zones.post_scan_waiting += 1
        self._log_flow(
            self.env.now, "move", fr=from_node, to="post_scan_waiting", n=1
        )
        if self.flow:
            self.flow.stage_start(item_id, "wash", self.env.now)
        yield done
        self._update_washer_busy_flags()
        if self.flow:
            self.flow.stage_complete(item_id, "wash", self.env.now)

    def _intake_wash_and_separate(self, item_id: int):
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

        self.zones.pre_scan_waiting += 1
        self._log_flow(
            self.env.now, "move", fr="truck_in", to="pre_scan_waiting", n=1
        )

        if self._scan_stations:
            self.zones.pre_scan_waiting -= 1
            scan_st = min(
                self._scan_stations, key=lambda s: len(s.wait_fifo) + s.service.count
            )
            if self.flow:
                self.flow.stage_start(item_id, "scan_in", self.env.now)
            yield scan_st.submit(item_id)
            if self.flow:
                self.flow.stage_complete(item_id, "scan_in", self.env.now)
            yield from self._transfer_delay("after_scan_in")
            yield from self._transfer_delay("to_wash")
            yield from self._wait_through_wash(item_id, from_node="scan_in")
        else:
            self.zones.pre_scan_waiting -= 1
            yield from self._transfer_delay("to_wash")
            yield from self._wait_through_wash(item_id, from_node="pre_scan_waiting")

        washer_id = self.zones.item_washer_line.get(item_id, self._washer_lines[0].washer_id)
        yield from self._transfer_delay("after_wash")
        self._log_flow(
            self.env.now,
            "move",
            fr=washer_id,
            to="separation_backlog",
            n=1,
        )
        yield from self._run_worker_station(
            item_id, "separation", from_node=washer_id
        )
        self._sync_separation_backlog()

    def _qc_loop(self, item_id: int, cycle: int, from_node: str = "general_press"):
        policy = self.config.policies.qc_rework
        yield from self._run_stage(item_id, "final_qc", from_node=from_node)
        defect = self.config.stages.final_qc.defect_rate or 0.0
        if self.rng.random() < defect and cycle < policy.max_cycles:
            self.metrics.qc_rework_cycles += 1
            if self.flow:
                self.flow.mark_rework(item_id)
                self.flow.set_qc_path(item_id, "rework_path")
            yield from self._transfer_delay("after_spotting")
            yield from self._run_worker_station(item_id, "spotting", from_node="final_qc")
            yield from self._transfer_delay("after_general_press")
            yield from self._run_general_press(item_id, from_node="press_conveyor")
            yield from self._qc_loop(item_id, cycle + 1, from_node="general_press")
            return

        yield from self._transfer_delay("after_final_qc")
        if self.config.stages.delivery_scan.enabled:
            yield from self._run_stage(item_id, "delivery_scan", from_node="final_qc")
            yield from self._transfer_delay("after_delivery_scan")
            if self.config.stages.outbound_scan.enabled:
                yield from self._run_stage(
                    item_id, "outbound_scan", from_node="delivery_scan"
                )
                yield from self._transfer_delay("after_outbound_scan")
            self.metrics.record_delivery_ready(
                self.env.now,
                day_open_minutes(self.config.calendar),
            )
        self.metrics.items_completed += 1

    def _wip_spotting_pipeline(self, item_id: int):
        """Mid-spotting WIP: enters spotting directly, not via separation backlog (washer output)."""
        if self.flow:
            self.flow.stage_complete(item_id, "scan_in", self.env.now)
            self.flow.stage_complete(item_id, "wash", self.env.now)
            self.flow.stage_complete(item_id, "separation", self.env.now)
            self.flow.set_qc_path(item_id, "spotting_path")
        yield from self._run_worker_station(item_id, "spotting", from_node="wip_spotting")
        yield from self._transfer_delay("after_spotting")
        yield from self._run_general_press(item_id, from_node="press_conveyor")
        yield from self._qc_loop(item_id, cycle=0)

    def _item_pipeline(self, item_id: int):
        loss_rate = effective_loss_rate(self.config)
        if self.rng.random() < loss_rate:
            self.metrics.items_lost += 1
            return

        if self.flow and not self._scan_in_enabled:
            self.flow.stage_complete(item_id, "scan_in", self.env.now)

        yield from self._intake_wash_and_separate(item_id)
        yield from self._transfer_delay("after_separation")

        path = self._route_after_separation()
        before_qc = "separation_backlog"
        if path == "spotting":
            if self.flow:
                self.flow.set_qc_path(item_id, "spotting_path")
            yield from self._run_worker_station(
                item_id, "spotting", from_node="separation_backlog"
            )
            yield from self._transfer_delay("after_spotting")
            yield from self._run_general_press(item_id, from_node="press_conveyor")
            yield from self._transfer_delay("after_general_press")
            before_qc = "general_press"
        elif path == "steam_tunnel":
            yield from self._run_stage(
                item_id, "steam_tunnel", from_node="separation_backlog"
            )
            yield from self._transfer_delay("after_steam_tunnel")
            yield from self._steam_post_check(item_id)
            if self._route_after_steam() == "general_press":
                if self.flow:
                    self.flow.set_qc_path(item_id, "steam_press_path")
                yield from self._run_general_press(
                    item_id, from_node="steam_exit_check"
                )
                yield from self._transfer_delay("after_general_press")
                before_qc = "general_press"
            else:
                if self.flow:
                    self.flow.set_qc_path(item_id, "steam_direct_path")
                before_qc = "steam_exit_check"
        elif path == "jacket_press":
            if self.flow:
                self.flow.set_qc_path(item_id, "jacket_path")
            yield from self._run_worker_station(
                item_id, "jacket_press", from_node="separation_backlog"
            )
            yield from self._transfer_delay("after_jacket_press")
            before_qc = "jacket_press"
        else:
            if self.flow:
                self.flow.set_qc_path(item_id, "press_path")
            yield from self._run_general_press(
                item_id, from_node="separation_backlog"
            )
            yield from self._transfer_delay("after_general_press")
            before_qc = "general_press"

        yield from self._qc_loop(item_id, cycle=0, from_node=before_qc)

    def _run_general_press(self, item_id: int, from_node: str):
        """Route into press line conveyor, then a presser wait queue."""
        if from_node not in ("press_conveyor",):
            self._log_flow(
                self.env.now, "move", fr=from_node, to="press_conveyor", n=1
            )
            from_node = "press_conveyor"
        yield from self._run_worker_station(
            item_id, "general_press", from_node=from_node
        )

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
            self.zones.trucks_arrived += wave.truck_count
            self._log_flow(
                self.env.now,
                "truck_arrival",
                count=wave.truck_count,
                fr="truck_in",
            )
            self._inject_items(wave.total_items(self.config.items_per_truck))

    def _report_progress(
        self,
        callback: Callable[[str, int, int, str], None] | None,
        phase: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if callback is not None:
            callback(phase, current, total, message)

    def run(
        self,
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        drain_sim_days: float | None = None,
    ) -> SimulationResult:
        cal = self.config.calendar
        days_to_sim = self.config.objectives.simulation_days
        operating_count = 0
        calendar_day = 0
        weekday = 0
        wip_seeded = False
        first_op_calendar_day: int | None = None
        last_op_calendar_day: int | None = None

        if self.sample_interval_minutes and self.sample_interval_minutes > 0:
            self.env.process(self._queue_sampler())

        while operating_count < days_to_sim:
            if is_operating_day(weekday, cal):
                if first_op_calendar_day is None:
                    first_op_calendar_day = calendar_day
                last_op_calendar_day = calendar_day
                if not wip_seeded:
                    self._seed_wip()
                    wip_seeded = True
                day_name = DAY_NAMES[weekday]
                for wave in self._schedule_by_weekday.get(day_name, []):
                    self.env.process(self._truck_arrival(wave, calendar_day))
                operating_count += 1
                self._report_progress(
                    progress_callback,
                    "simulate",
                    operating_count,
                    days_to_sim,
                    f"Operating day {operating_count}/{days_to_sim}",
                )

            calendar_day += 1
            end = calendar_day * 24 * 60
            if end > self.env.now:
                self.env.run(until=end)
            weekday = (weekday + 1) % 7

        if self.env.now < calendar_day * 24 * 60:
            self.env.run(until=calendar_day * 24 * 60)

        if first_op_calendar_day is not None and last_op_calendar_day is not None:
            pb_start, pb_end = operating_window_bounds(
                first_op_calendar_day, last_op_calendar_day, cal
            )
            self.metrics.playback_start_minutes = pb_start
            self.metrics.playback_horizon_minutes = pb_end
        else:
            self.metrics.playback_start_minutes = 0.0
            self.metrics.playback_horizon_minutes = self.env.now

        drain_days = 16.0 if drain_sim_days is None else drain_sim_days
        self._report_progress(
            progress_callback, "drain", 0, 1, "Draining WIP…"
        )
        drain_until = self.env.now + 24 * 60 * drain_days
        self.env.run(until=drain_until)
        self._report_progress(
            progress_callback, "drain", 1, 1, "Drain complete"
        )

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
    sample_interval_minutes: float | None = None,
    *,
    viz_mode: bool = False,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> SimulationResult:
    root = project_root or Path(__file__).resolve().parents[2]
    sim = PlantSimulation(
        config,
        root,
        seed=seed,
        track_flow=track_flow,
        sample_interval_minutes=sample_interval_minutes,
        record_flow_events=not viz_mode,
    )
    drain_days = 1.0 if viz_mode else None
    return sim.run(
        progress_callback=progress_callback,
        drain_sim_days=drain_days,
    )
