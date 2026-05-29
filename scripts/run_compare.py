#!/usr/bin/env python3
"""Compare baseline vs scenario overlays."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plant_sim.engine import run_simulation
from plant_sim.report import payback_years, write_scorecard
from plant_sim.scenarios import list_scenarios, load_scenario


def _annual_savings(baseline_labor: float, scenario_labor: float, opex_delta: float) -> float:
    daily = baseline_labor - scenario_labor
    return daily * 260 - opex_delta


def main() -> int:
    scenarios = ["baseline"] + list_scenarios(ROOT)
    rows: list[dict] = []
    baseline_labor: float | None = None

    for name in scenarios:
        scenario_key = None if name == "baseline" else name
        config = load_scenario(scenario_key, ROOT)
        result = run_simulation(config, project_root=ROOT)
        s = result.summary
        labor = s["total_labor_cost"]
        if name == "baseline":
            baseline_labor = labor

        savings = 0.0
        pb = None
        if baseline_labor is not None and name != "baseline":
            opex = config.economics.opex_annual
            savings = _annual_savings(baseline_labor, labor, opex)
            pb = payback_years(config.economics.capex, savings)

        rows.append(
            {
                "scenario": name,
                "items_completed": s["items_completed"],
                "items_injected": s["items_injected"],
                "items_lost_estimate": s["items_lost_estimate"],
                "delivery_ready_time": s["delivery_ready_time"],
                "delivery_ready_by_deadline": s["delivery_ready_by_deadline"],
                "total_labor_cost": labor,
                "economics_capex": s["economics_capex"],
                "economics_opex_annual": s["economics_opex_annual"],
                "estimated_annual_savings": round(savings, 2),
                "payback_years": round(pb, 2) if pb is not None else None,
                "top_bottleneck": s["bottlenecks"][0] if s["bottlenecks"] else None,
            }
        )

    path = write_scorecard(rows, ROOT, name="compare")
    print("Scenario comparison scorecard")
    print("-" * 72)
    for row in rows:
        print(
            f"{row['scenario']:10}  completed={row['items_completed']:6}  "
            f"labor=${row['total_labor_cost']:,.0f}  "
            f"ready={row['delivery_ready_time']}  "
            f"lost≈{row['items_lost_estimate']}"
        )
        if row.get("payback_years") is not None:
            print(f"           payback≈{row['payback_years']} yr  capex=${row['economics_capex']:,.0f}")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
