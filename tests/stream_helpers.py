"""Helpers for streaming viz API tests."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request


def consume_sim_stream(
    base_url: str,
    job_id: str,
    *,
    timeout_sec: float = 180,
) -> tuple[dict, list[dict]]:
    """Read SSE until sim_done; return (assembled graph or done payload, batches)."""
    url = (
        f"{base_url}/api/sim/stream?"
        + urllib.parse.urlencode({"job_id": job_id})
    )
    deadline = time.time() + timeout_sec
    batches: list[dict] = []
    init_payload: dict | None = None
    result: dict | None = None

    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    status_url = (
        f"{base_url}/api/simulate/status?"
        + urllib.parse.urlencode({"job_id": job_id})
    )

    def _poll_done_graph() -> dict | None:
        try:
            with urllib.request.urlopen(status_url, timeout=10) as sresp:
                snap = json.loads(sresp.read())
            if snap.get("status") == "done" and snap.get("result"):
                return snap["result"]
        except OSError:
            pass
        return None

    with urllib.request.urlopen(req, timeout=timeout_sec + 5) as resp:
        event_type = ""
        data_lines: list[str] = []
        while time.time() < deadline:
            try:
                line = resp.readline()
            except (TimeoutError, OSError):
                polled = _poll_done_graph()
                if polled is not None:
                    result = polled
                    break
                continue
            if not line:
                if result is not None:
                    break
                polled = _poll_done_graph()
                if polled is not None:
                    result = polled
                    break
                time.sleep(0.1)
                continue
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text.startswith("event:"):
                event_type = text.split(":", 1)[1].strip()
                continue
            if text.startswith("data:"):
                data_lines.append(text.split(":", 1)[1].strip())
                continue
            if text == "" and data_lines:
                raw = "\n".join(data_lines)
                data_lines = []
                payload = json.loads(raw)
                if event_type == "sim_init":
                    init_payload = payload
                elif event_type == "sim_batch":
                    batches.append(payload)
                elif event_type == "sim_done":
                    result = payload
                    break
                elif event_type == "sim_error":
                    raise AssertionError(payload.get("error", "stream error"))
                event_type = ""

    if result is None:
        raise TimeoutError(f"stream for job {job_id} did not finish")
    return result, batches


def start_sim_job(
    base_url: str,
    *,
    seed: int = 42,
    sample_interval_minutes: float = 30,
    config: dict | None = None,
    config_overrides: dict | None = None,
    batch_size: int = 20,
) -> str:
    body: dict = {
        "seed": seed,
        "sample_interval_minutes": sample_interval_minutes,
        "batch_size": batch_size,
    }
    if config is not None:
        body["config"] = config
    else:
        body["config_overrides"] = config_overrides or {}
    req = urllib.request.Request(
        f"{base_url}/api/sim/start",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        started = json.loads(resp.read())
    job_id = started.get("job_id")
    if not job_id:
        raise AssertionError("missing job_id")
    return job_id


def poll_sim_job(base_url: str, job_id: str, *, timeout_sec: float = 180) -> dict:
    """Poll legacy status endpoint until result is ready."""
    deadline = time.time() + timeout_sec
    status_url = (
        f"{base_url}/api/simulate/status?"
        + urllib.parse.urlencode({"job_id": job_id})
    )
    while time.time() < deadline:
        with urllib.request.urlopen(status_url, timeout=10) as resp:
            snap = json.loads(resp.read())
        if snap.get("status") == "done":
            result = snap.get("result")
            if result is None:
                raise AssertionError("job done but missing result")
            return result
        if snap.get("status") == "error":
            raise AssertionError(snap.get("error", "simulation failed"))
        time.sleep(0.15)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_sec}s")
