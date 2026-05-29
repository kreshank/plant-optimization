"""Write simulation outputs to disk."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from plant_sim.engine import SimulationResult


def outputs_dir(root: Path) -> Path:
    out = root / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_result(
    result: SimulationResult,
    root: Path,
    label: str = "baseline",
) -> Path:
    out_dir = outputs_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{label}_{stamp}.json"
    payload = {
        "label": label,
        "summary": result.summary,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_scorecard(
    rows: list[dict],
    root: Path,
    name: str = "compare",
) -> Path:
    out_dir = outputs_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{name}_{stamp}.json"
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path


def payback_years(capex: float, annual_savings: float) -> float | None:
    if annual_savings <= 0:
        return None
    return capex / annual_savings
