"""HTTP smoke tests for the served visualizer API."""

from __future__ import annotations

import json
import time
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def viz_server():
    if not (ROOT / "config" / "baseline.yaml").exists():
        pytest.skip("baseline.yaml not present")
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from serve_viz import VizHandler

    server = ThreadingHTTPServer(("127.0.0.1", 0), VizHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_health(viz_server: str) -> None:
  with urllib.request.urlopen(f"{viz_server}/api/health", timeout=5) as resp:
    data = json.loads(resp.read())
  assert data.get("ok") is True


def _poll_sim_job(base_url: str, job_id: str, *, timeout_sec: float = 180) -> dict:
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


def test_post_simulate_contract(viz_server: str) -> None:
  body = json.dumps(
    {"seed": 42, "sample_interval_minutes": 30, "config_overrides": {}}
  ).encode()
  req = urllib.request.Request(
    f"{viz_server}/api/simulate",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
  )
  with urllib.request.urlopen(req, timeout=30) as resp:
    started = json.loads(resp.read())
  assert resp.status == 200
  assert started.get("job_id")
  data = _poll_sim_job(viz_server, started["job_id"])
  assert "error" not in data
  assert len(data.get("groups", [])) >= 5
  assert data.get("time_series", {}).get("samples")
  sep = next(g for g in data["groups"] if g["id"] == "separation")
  assert sep.get("group_backlog")
  assert sep.get("backlog_panel_px", 0) > 0
  block_ids = {b["id"] for g in data["groups"] for b in g.get("blocks", [])}
  assert "separation_backlog" not in block_ids
  group_ids = {g["id"] for g in data["groups"]}
  assert "outbound_scan" in group_ids
  assert "final_qc" in group_ids
  assert "delivery_scan" in group_ids
  wash = next(g for g in data["groups"] if g["id"] == "wash")
  assert wash.get("group_backlog", {}).get("metric") == "zone:post_scan_waiting"
  qc = next(g for g in data["groups"] if g["id"] == "final_qc")
  assert qc.get("group_backlog", {}).get("metric") == "stage:final_qc"
  delivery = next(g for g in data["groups"] if g["id"] == "delivery_scan")
  assert delivery.get("group_backlog", {}).get("metric") == "stage:delivery_scan"
  link_pairs = {(lnk["from"], lnk["to"]) for lnk in data.get("group_links", [])}
  assert ("delivery_scan", "outbound_scan") in link_pairs
  assert ("outbound_scan", "outbound") in link_pairs


def test_index_html(viz_server: str) -> None:
  with urllib.request.urlopen(f"{viz_server}/", timeout=5) as resp:
    html = resp.read().decode()
  assert "flow-canvas" in html
  assert "app.js" in html
