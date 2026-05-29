#!/usr/bin/env python3
"""Smoke test: POST /api/sim/start and consume SSE batches."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from serve_viz import VizHandler
from stream_helpers import consume_sim_stream, start_sim_job


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), VizHandler)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)

    with urllib.request.urlopen(f"{base}/api/config", timeout=5) as resp:
        cfg = json.loads(resp.read())
    cfg["objectives"]["simulation_days"] = 1
    job_id = start_sim_job(
        base, seed=42, sample_interval_minutes=15, config=cfg, batch_size=8
    )
    _done, batches = consume_sim_stream(base, job_id, timeout_sec=120)
    assert batches, "expected at least one sim_batch"
    assert batches[0].get("steps"), "batch missing steps"
    print(f"ok: {len(batches)} batches, last event done")
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
