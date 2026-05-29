#!/usr/bin/env python3
"""Run baseline plant simulation from config/baseline.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.engine import run_simulation
from plant_sim.report import write_result
from plant_sim.scenarios import load_scenario


def main() -> int:
    config = load_scenario(None, ROOT)
    result = run_simulation(config, project_root=ROOT)
    path = write_result(result, ROOT, label="baseline")
    print("Plant simulation — baseline")
    print("-" * 40)
    for key, value in result.summary.items():
        if key != "stages" and key != "bottlenecks":
            print(f"  {key}: {value}")
    print("\nTop bottlenecks:")
    for row in result.summary.get("bottlenecks", [])[:5]:
        print(f"  {row['stage']}: {row['utilization']:.1%}")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
