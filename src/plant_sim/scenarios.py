"""Load baseline config and apply scenario overlays."""

from __future__ import annotations

from pathlib import Path

from plant_sim.config_models import PlantConfig, load_plant_config


def project_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def baseline_path(root: Path | None = None) -> Path:
    root = root or project_root_from_here()
    return root / "config" / "baseline.yaml"


def scenario_path(name: str, root: Path | None = None) -> Path:
    root = root or project_root_from_here()
    return root / "config" / "scenarios" / f"{name}.yaml"


def load_scenario(name: str | None, root: Path | None = None) -> PlantConfig:
    root = root or project_root_from_here()
    base = baseline_path(root)
    overlay = scenario_path(name, root) if name else None
    return load_plant_config(base, overlay)


def list_scenarios(root: Path | None = None) -> list[str]:
    root = root or project_root_from_here()
    folder = root / "config" / "scenarios"
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.yaml"))
