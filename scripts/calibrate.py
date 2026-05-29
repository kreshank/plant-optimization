#!/usr/bin/env python3
"""Validate config and truck schedule; run baseline for calibration days."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.config_models import PlantConfig, load_plant_config
from plant_sim.engine import run_simulation
from plant_sim.schedule import load_truck_schedule


def main() -> int:
    base = ROOT / "config" / "baseline.yaml"
    config = load_plant_config(base, project_root=ROOT)
    waves = load_truck_schedule(config, ROOT)

    print("Calibration / validation")
    print("-" * 40)
    print(f"Config valid: {base.name}")
    print(f"Truck waves loaded: {len(waves)}")
    by_day: dict[str, float] = {}
    for w in waves:
        if w.direction != "incoming":
            continue
        by_day[w.day_of_week] = by_day.get(w.day_of_week, 0) + w.total_items(
            config.items_per_truck
        )
    print("Incoming items by weekday:")
    for day, items in sorted(by_day.items()):
        print(f"  {day}: {items:,.0f}")

    result = run_simulation(config, project_root=ROOT)
    print(f"\nSimulation ({config.objectives.simulation_days} operating days):")
    print(f"  Injected:  {result.summary['items_injected']:,.0f}")
    print(f"  Completed: {result.summary['items_completed']:,}")
    print(f"  Deferred wash events: {result.summary['items_deferred_wash']}")
    if "daily_items_target" in result.summary:
        print(f"  Daily target: {result.summary['daily_items_target']:,.0f}")
        print(f"  Gap vs target: {result.summary.get('daily_items_gap', 0):,.0f}")
    print("\nTop bottlenecks:")
    for row in result.summary.get("bottlenecks", [])[:5]:
        print(f"  {row['stage']}: {row['utilization']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
