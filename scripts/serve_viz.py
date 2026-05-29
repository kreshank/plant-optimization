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
from plant_sim.engine import run_simulation
from plant_sim.viz_builder import build_flow_graph, config_to_jsonable

VIZ_DIR = ROOT / "viz"
DEFAULT_PORT = 8765
JOB_TTL_SEC = 3600

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        out: dict[str, Any] = {
            "job_id": job_id,
            "status": job["status"],
            "phase": job.get("phase", ""),
            "current": job.get("current", 0),
            "total": max(job.get("total", 1), 1),
            "message": job.get("message", ""),
        }
        if job["status"] == "done":
            out["result"] = job.get("result")
        if job["status"] == "error":
            out["error"] = job.get("error", "unknown error")
        return out


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


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


def _run_sim_job(
    job_id: str,
    config: PlantConfig,
    *,
    seed: int,
    interval: float,
) -> None:
    def progress(phase: str, current: int, total: int, message: str) -> None:
        _update_job(
            job_id,
            status="running",
            phase=phase,
            current=current,
            total=max(total, 1),
            message=message,
        )

    try:
        result = run_simulation(
            config,
            project_root=ROOT,
            seed=seed,
            track_flow=False,
            sample_interval_minutes=interval,
            viz_mode=True,
            progress_callback=progress,
        )
        _update_job(
            job_id,
            phase="build_graph",
            current=0,
            total=1,
            message="Building graph…",
        )
        graph = build_flow_graph(config, result)
        _update_job(
            job_id,
            status="done",
            phase="done",
            current=1,
            total=1,
            message="Complete",
            result=graph,
        )
    except Exception as e:
        _update_job(
            job_id,
            status="error",
            phase="error",
            error=str(e),
            message=str(e),
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

    def _start_sim_job(
        self,
        *,
        seed: int,
        interval: float,
        overrides: dict | None,
    ) -> str:
        _prune_old_jobs()
        config = self._load_config(overrides)
        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "running",
                "phase": "simulate",
                "current": 0,
                "total": max(config.objectives.simulation_days, 1),
                "message": "Starting…",
                "created_at": time.time(),
            }
        thread = threading.Thread(
            target=_run_sim_job,
            args=(job_id, config),
            kwargs={"seed": seed, "interval": interval},
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
                job_id = self._start_sim_job(
                    seed=seed, interval=interval, overrides=overrides or None
                )
                self._send_json({"job_id": job_id})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/simulate":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        try:
            overrides = payload.get("config_overrides", {})
            seed = int(payload.get("seed", 42))
            interval = float(payload.get("sample_interval_minutes", 1))
            job_id = self._start_sim_job(
                seed=seed,
                interval=interval,
                overrides=overrides or None,
            )
            self._send_json({"job_id": job_id})
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)


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
    print("Loads config/baseline.yaml — edit locally and click Refresh")
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
