"""HTTP handler wiring for serve_viz (no live server)."""

import importlib.util
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_serve_viz():
    path = ROOT / "scripts" / "serve_viz.py"
    spec = importlib.util.spec_from_file_location("serve_viz", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_handler_uses_class_paths_not_partial_kwargs():
    mod = _load_serve_viz()
    sig = inspect.signature(mod.VizHandler.__init__)
    assert "root" not in sig.parameters
    assert mod.VizHandler.root == mod.ROOT
    assert mod.VizHandler.viz_dir == mod.VIZ_DIR


def test_job_snapshot_lists_checkpoint_steps_only():
    mod = _load_serve_viz()
    job_id = "handler-test-job"
    with mod._jobs_lock:
        mod._jobs[job_id] = {
            "status": "running",
            "phase": "simulate",
            "current": 0,
            "total": 1,
            "message": "",
            "generation": 0,
            "branch_id": 0,
            "checkpoints": {"3": {"snapshot": {"zones": {}}}},
            "batches": [],
        }
    try:
        snap = mod._job_snapshot(job_id)
        assert snap is not None
        assert snap["checkpoint_steps"] == [3]
        assert "checkpoints" not in snap
    finally:
        with mod._jobs_lock:
            mod._jobs.pop(job_id, None)
