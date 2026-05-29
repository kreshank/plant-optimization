#!/usr/bin/env python3
"""Sweep parameters listed under optimization.bounds in baseline config."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.config_models import PlantConfig, load_yaml
from plant_sim.engine import run_simulation
from plant_sim.report import write_scorecard


def _set_by_path(data: dict, path: str, value: float | int) -> None:
    keys = path.split(".")
    cur = data
    for k in keys[:-1]:
        if k not in cur:
            cur[k] = {}
        cur = cur[k]
    leaf = keys[-1]
    parent = cur
    if leaf not in parent and keys[-2] == "workers":
        parent[leaf] = value
    else:
        parent[leaf] = value


def _expand_bounds(config: PlantConfig) -> list[tuple[str, list[float | int]]]:
    return [(path, bounds) for path, bounds in config.optimization.bounds.items()]


def main() -> int:
    base_path = ROOT / "config" / "baseline.yaml"
    data = load_yaml(base_path)
    config = PlantConfig.model_validate(data)
    bounds_list = _expand_bounds(config)

    if not bounds_list:
        print("No optimization.bounds defined in config.")
        return 1

    rows: list[dict] = []
    for path, bounds in bounds_list:
        if len(bounds) != 2:
            continue
        lo, hi = bounds
        for value in [lo, (lo + hi) / 2, hi]:
            trial = copy.deepcopy(data)
            _set_by_path(trial, path, int(value) if isinstance(lo, int) else value)
            trial_config = PlantConfig.model_validate(trial)
            result = run_simulation(trial_config, project_root=ROOT, seed=42)
            rows.append(
                {
                    "parameter": path,
                    "value": value,
                    "items_completed": result.summary["items_completed"],
                    "total_labor_cost": result.summary["total_labor_cost"],
                    "top_bottleneck": result.summary["bottlenecks"][0]
                    if result.summary["bottlenecks"]
                    else None,
                }
            )

    path = write_scorecard(rows, ROOT, name="sweep")
    print(f"Sweep completed: {len(rows)} runs → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
