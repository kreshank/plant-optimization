"""Load truck schedule from CSV referenced in config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from plant_sim.config_models import PlantConfig
from plant_sim.time_utils import parse_time_of_day, time_to_minutes


@dataclass(frozen=True)
class TruckWave:
    day_of_week: str
    arrival_time: str
    truck_count: int
    direction: str
    items_per_truck: float | None = None

    @property
    def arrival_minutes(self) -> float:
        return time_to_minutes(parse_time_of_day(self.arrival_time))

    def total_items(self, default_items_per_truck: float) -> float:
        per_truck = (
            self.items_per_truck
            if self.items_per_truck is not None
            else default_items_per_truck
        )
        return self.truck_count * per_truck


def load_truck_schedule(config: PlantConfig, project_root: Path) -> list[TruckWave]:
    path = Path(config.inputs.truck_schedule)
    if not path.is_absolute():
        path = project_root / path
    if not path.exists():
        raise FileNotFoundError(f"Truck schedule not found: {path}")
    df = pd.read_csv(path)
    required = {"day_of_week", "arrival_time", "truck_count", "direction"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"truck_schedule missing columns: {missing}")
    waves: list[TruckWave] = []
    for _, row in df.iterrows():
        items = None
        if "items_per_truck" in df.columns and pd.notna(row.get("items_per_truck")):
            items = float(row["items_per_truck"])
        waves.append(
            TruckWave(
                day_of_week=str(row["day_of_week"]).lower(),
                arrival_time=str(row["arrival_time"]),
                truck_count=int(row["truck_count"]),
                direction=str(row["direction"]).lower(),
                items_per_truck=items,
            )
        )
    return waves
