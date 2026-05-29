#!/usr/bin/env python3
"""Serve plant flow visualizer and API (loads local config/baseline.yaml)."""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.config_models import PlantConfig, load_yaml
from plant_sim.checkpoint import Checkpoint
from plant_sim.sim_stream import run_simulation_streaming
from plant_sim.stream_assembly import build_graph_from_stream, sim_init_payload
from plant_sim.viz_builder import config_to_jsonable

VIZ_DIR = ROOT / "viz"
DEFAULT_PORT = 8765
JOB_TTL_SEC = 3600
DEFAULT_BATCH_SIZE = 20

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return None
            checkpoint_steps = sorted(
                int(k) for k in list((job.get("checkpoints") or {}).keys())
            )
            status = job["status"]
            phase = job.get("phase", "")
            current = job.get("current", 0)
            total = max(job.get("total", 1), 1)
            message = job.get("message", "")
            generation = job.get("generation", 0)
            branch_id = job.get("branch_id", 0)
            batch_count = len(job.get("batches") or [])
            result = job.get("result") if status == "done" else None
            error = job.get("error") if status == "error" else None
        out: dict[str, Any] = {
            "job_id": job_id,
            "status": status,
            "phase": phase,
            "current": current,
            "total": total,
            "message": message,
            "generation": generation,
            "branch_id": branch_id,
            "checkpoint_steps": checkpoint_steps,
            "batch_count": batch_count,
        }
        if result is not None:
            out["result"] = result
        if error is not None:
            out["error"] = error
        return out


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _append_sse_event(job_id: str, event: str, data: dict) -> None:
    line = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.setdefault("sse_log", []).append(line)
        job["sse_notify"] = job.get("sse_notify", 0) + 1


def _prune_old_jobs() -> None:
    now = time.time()
    with _jobs_lock:
        stale = [
            jid
            for jid, j in _jobs.items()
            if now - j.get("created_at", now) > JOB_TTL_SEC
        ]
        for jid in stale:
            del _jobs[jid]


def _run_stream_job_inner(
    job_id: str,
    config: PlantConfig,
    *,
    seed: int,
    interval: float,
    batch_size: int,
    branch_id: int,
    generation: int,
    initial_checkpoint: dict | None = None,
    fork_index: int = -1,
    is_branch: bool = False,
) -> None:
    batches: list[dict] = []
    checkpoints: dict[str, dict] = {}

    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            checkpoints = dict(job.get("checkpoints") or {})
            if is_branch:
                batches = [b for b in job.get("batches", []) if b.get("step_to", 0) <= fork_index]

    def progress(phase: str, current: int, total: int, message: str) -> None:
        _update_job(
            job_id,
            status="running",
            phase=phase,
            current=current,
            total=max(total, 1),
            message=message,
        )

    def on_checkpoint(step_i: int, cp_dict: dict) -> None:
        checkpoints[str(step_i)] = cp_dict
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["checkpoints"] = checkpoints

    def on_batch(batch: dict) -> None:
        batches.append(batch)
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["batches"] = batches
        _append_sse_event(job_id, "sim_batch", batch)

    def cancel_check() -> bool:
        with _jobs_lock:
            job = _jobs.get(job_id)
            return bool(job and job.get("cancel_requested"))

    try:
        if is_branch:
            _append_sse_event(
                job_id,
                "sim_fork",
                {
                    "fork_index": fork_index,
                    "branch_id": branch_id,
                    "generation": generation,
                },
            )
            _append_sse_event(
                job_id,
                "sim_init",
                sim_init_payload(config, job_id, branch_id=branch_id),
            )
        else:
            _append_sse_event(
                job_id,
                "sim_init",
                sim_init_payload(config, job_id, branch_id=branch_id),
            )

        cp = Checkpoint.from_dict(initial_checkpoint) if initial_checkpoint else None
        result = run_simulation_streaming(
            config,
            ROOT,
            seed=seed,
            sample_interval_minutes=interval,
            on_batch=on_batch,
            batch_size=batch_size,
            viz_mode=True,
            progress_callback=progress,
            cancel_check=cancel_check,
            on_checkpoint=on_checkpoint,
            initial_checkpoint=cp,
            fork_index=fork_index,
            branch_id=branch_id,
            generation=generation,
        )
        _update_job(
            job_id,
            phase="build_graph",
            current=0,
            total=1,
            message="Building graph…",
        )
        graph = build_graph_from_stream(config, result, batches)
        _update_job(
            job_id,
            status="done",
            phase="done",
            current=1,
            total=1,
            message="Complete",
            result=graph,
            batches=batches,
            branch_id=branch_id,
            generation=generation,
        )
        _append_sse_event(
            job_id,
            "sim_done",
            {"job_id": job_id, "summary": graph.get("summary", {}), "branch_id": branch_id},
        )
    except Exception as e:
        _update_job(
            job_id,
            status="error",
            phase="error",
            error=str(e),
            message=str(e),
        )
        _append_sse_event(job_id, "sim_error", {"error": str(e)})


def _run_stream_job(
    job_id: str,
    config: PlantConfig,
    *,
    seed: int,
    interval: float,
    batch_size: int,
) -> None:
    _run_stream_job_inner(
        job_id,
        config,
        seed=seed,
        interval=interval,
        batch_size=batch_size,
        branch_id=0,
        generation=0,
    )


def _run_branch_job(
    job_id: str,
    config: PlantConfig,
    *,
    seed: int,
    interval: float,
    batch_size: int,
    fork_index: int,
    checkpoint: dict,
    branch_id: int,
    generation: int,
) -> None:
    _run_stream_job_inner(
        job_id,
        config,
        seed=seed,
        interval=interval,
        batch_size=batch_size,
        branch_id=branch_id,
        generation=generation,
        initial_checkpoint=checkpoint,
        fork_index=fork_index,
        is_branch=True,
    )


class VizHandler(SimpleHTTPRequestHandler):
    root: Path = ROOT
    viz_dir: Path = VIZ_DIR

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.viz_dir), **kwargs)

    def log_message(self, format: str, *args) -> None:
        if self.path.startswith("/api"):
            super().log_message(format, *args)

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_stream(self, job_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        sent_idx = 0
        deadline = time.time() + 300
        while time.time() < deadline:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    self.wfile.write(
                        b'event: sim_error\ndata: {"error":"job not found"}\n\n'
                    )
                    self.wfile.flush()
                    return
                log = job.get("sse_log", [])
                chunk = "".join(log[sent_idx:])
                status = job.get("status")
                log_len = len(log)
            if chunk:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
                sent_idx = log_len
            if status in ("done", "error") and sent_idx >= log_len:
                return
            time.sleep(0.05)

    def _load_config(self, overrides: dict | None = None) -> PlantConfig:
        base_path = self.root / "config" / "baseline.yaml"
        if not base_path.exists():
            raise FileNotFoundError(
                f"Missing {base_path}. Copy from config.example/baseline.yaml"
            )
        data = load_yaml(base_path)
        if overrides:
            data = _deep_merge(data, overrides)
        return PlantConfig.model_validate(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _resolve_config(
        self,
        overrides: dict | None = None,
        full_config: dict | None = None,
    ) -> PlantConfig:
        if full_config is not None:
            return PlantConfig.model_validate(full_config)
        return self._load_config(overrides)

    def _start_stream_job(
        self,
        *,
        seed: int,
        interval: float,
        batch_size: int,
        overrides: dict | None = None,
        full_config: dict | None = None,
    ) -> str:
        _prune_old_jobs()
        config = self._resolve_config(overrides, full_config)
        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "running",
                "phase": "simulate",
                "current": 0,
                "total": max(config.objectives.simulation_days, 1),
                "message": "Starting…",
                "created_at": time.time(),
                "generation": 0,
                "branch_id": 0,
                "cancel_requested": False,
                "batches": [],
                "sse_log": [],
                "sse_notify": 0,
                "config": config,
                "checkpoints": {},
                "seed": seed,
                "interval": interval,
                "batch_size": batch_size,
            }
        thread = threading.Thread(
            target=_run_stream_job,
            args=(job_id, config),
            kwargs={"seed": seed, "interval": interval, "batch_size": batch_size},
            daemon=True,
        )
        thread.start()
        return job_id

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()

        if path == "/api/health":
            self._send_json({"ok": True})
            return

        if path == "/api/config":
            try:
                config = self._load_config()
                self._send_json(config_to_jsonable(config))
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        if path == "/api/sim/stream":
            qs = parse_qs(parsed.query)
            job_id = qs.get("job_id", [""])[0]
            if not job_id:
                self._send_json({"error": "job_id required"}, status=400)
                return
            try:
                self._send_sse_stream(job_id)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/simulate/status":
            qs = parse_qs(parsed.query)
            job_id = qs.get("job_id", [""])[0]
            if not job_id:
                self._send_json({"error": "job_id required"}, status=400)
                return
            snap = _job_snapshot(job_id)
            if snap is None:
                self._send_json({"error": "job not found"}, status=404)
                return
            self._send_json(snap)
            return

        if path == "/api/simulate":
            try:
                qs = parse_qs(parsed.query)
                seed = int(qs.get("seed", ["42"])[0])
                overrides: dict = {}
                if "items_per_truck" in qs:
                    overrides["items_per_truck"] = float(qs["items_per_truck"][0])
                if "simulation_days" in qs:
                    overrides.setdefault("objectives", {})
                    overrides["objectives"]["simulation_days"] = int(
                        qs["simulation_days"][0]
                    )
                interval = float(qs.get("sample_interval", ["1"])[0])
                batch_size = int(qs.get("batch_size", [str(DEFAULT_BATCH_SIZE)])[0])
                job_id = self._start_stream_job(
                    seed=seed,
                    interval=interval,
                    batch_size=batch_size,
                    overrides=overrides or None,
                )
                self._send_json({"job_id": job_id})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return

        if parsed.path == "/api/sim/cancel":
            job_id = payload.get("job_id", "")
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id]["cancel_requested"] = True
                    _jobs[job_id]["generation"] = _jobs[job_id].get("generation", 0) + 1
            self._send_json({"ok": True, "job_id": job_id})
            return

        if parsed.path == "/api/sim/branch":
            try:
                job_id = payload.get("job_id", "")
                fork_index = int(payload.get("fork_index", -1))
                full_config = payload.get("config")
                if not job_id or fork_index < 0 or full_config is None:
                    self._send_json(
                        {"error": "job_id, fork_index, and config required"},
                        status=400,
                    )
                    return
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    if job is None:
                        self._send_json({"error": "job not found"}, status=404)
                        return
                    job["cancel_requested"] = True
                    cps = job.get("checkpoints") or {}
                    cp = cps.get(str(fork_index))
                    if cp is None:
                        self._send_json(
                            {"error": f"no checkpoint at step {fork_index}"},
                            status=400,
                        )
                        return
                    branch_id = job.get("branch_id", 0) + 1
                    generation = job.get("generation", 0) + 1
                    job["branch_id"] = branch_id
                    job["generation"] = generation
                    job["cancel_requested"] = False
                    job["status"] = "running"
                    seed = int(job.get("seed", 42))
                    interval = float(job.get("interval", 1))
                    batch_size = int(job.get("batch_size", DEFAULT_BATCH_SIZE))
                config = self._resolve_config(full_config=full_config)
                thread = threading.Thread(
                    target=_run_branch_job,
                    args=(job_id, config),
                    kwargs={
                        "seed": seed,
                        "interval": interval,
                        "batch_size": batch_size,
                        "fork_index": fork_index,
                        "checkpoint": cp,
                        "branch_id": branch_id,
                        "generation": generation,
                    },
                    daemon=True,
                )
                thread.start()
                self._send_json(
                    {
                        "ok": True,
                        "job_id": job_id,
                        "branch_id": branch_id,
                        "generation": generation,
                        "fork_index": fork_index,
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        if parsed.path in ("/api/sim/start", "/api/simulate"):
            try:
                overrides = payload.get("config_overrides") or {}
                full_config = payload.get("config")
                seed = int(payload.get("seed", 42))
                interval = float(payload.get("sample_interval_minutes", 1))
                batch_size = int(payload.get("batch_size", DEFAULT_BATCH_SIZE))
                job_id = self._start_stream_job(
                    seed=seed,
                    interval=interval,
                    batch_size=batch_size,
                    overrides=overrides if overrides else None,
                    full_config=full_config,
                )
                self._send_json({"job_id": job_id, "generation": 0, "branch_id": 0})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        self.send_error(404)


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), VizHandler)
    url = f"http://127.0.0.1:{DEFAULT_PORT}/"
    print(f"Plant visualizer: {url}")
    print("Streaming sim via POST /api/sim/start + GET /api/sim/stream")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
