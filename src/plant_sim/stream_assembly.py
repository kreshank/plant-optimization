"""Assemble viz payloads for streaming simulation."""

from __future__ import annotations

from typing import Any

from plant_sim.config_models import PlantConfig
from plant_sim.engine import SimulationResult
from plant_sim.group_layout import build_group_layout
from plant_sim.metrics import MetricsCollector
from plant_sim.sim_stream import batches_to_samples
from plant_sim.viz_builder import build_flow_graph, config_to_jsonable


def _empty_result(config: PlantConfig) -> SimulationResult:
    metrics = MetricsCollector(config=config)
    return SimulationResult(config=config, metrics=metrics, summary={})


def sim_init_payload(
    config: PlantConfig, job_id: str, *, branch_id: int = 0
) -> dict[str, Any]:
    layout = build_group_layout(config, _empty_result(config))
    return {
        "job_id": job_id,
        "branch_id": branch_id,
        "groups": layout["groups"],
        "group_links": layout.get("group_links", []),
        "layout_meta": layout.get("layout_meta", {}),
        "effective_config": config_to_jsonable(config),
    }


def build_graph_from_stream(
    config: PlantConfig,
    result: SimulationResult,
    batches: list[dict[str, Any]],
) -> dict[str, Any]:
    graph = build_flow_graph(config, result)
    if batches:
        samples = batches_to_samples(batches)
        if samples and graph.get("time_series"):
            graph["time_series"] = {
                **graph["time_series"],
                "samples": samples,
                "sample_count": len(samples),
            }
    graph["stream_batches"] = len(batches)
    return graph
