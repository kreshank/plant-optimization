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
