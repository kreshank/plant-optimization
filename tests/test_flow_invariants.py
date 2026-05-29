"""Rigorous flow-ordering and pipeline invariant tests."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from plant_sim.config_models import PlantConfig, load_plant_config
from plant_sim.engine import run_simulation

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def _load_fixture(name: str) -> PlantConfig:
    return load_plant_config(FIXTURES / name, project_root=ROOT)


def _run_tracked(config: PlantConfig, seed: int = 0):
    return run_simulation(config, project_root=ROOT, seed=seed, track_flow=True)


class TestPerItemOrdering:
    def test_tiny_plant_no_flow_violations(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=1)
        assert result.flow is not None
        assert result.flow.ok, result.flow.violations[:10]

    def test_routing_plant_all_paths_no_violations(self):
        config = _load_fixture("routing_plant.yaml")
        result = _run_tracked(config, seed=42)
        assert result.flow is not None
        assert result.flow.ok, result.flow.violations[:10]

    def test_example_template_validates(self):
        example = ROOT / "config.example" / "baseline.yaml"
        if not example.exists():
            pytest.skip("config.example not present")
        config = load_plant_config(example, project_root=ROOT)
        assert config.objectives.simulation_days >= 1


class TestMassConservation:
    def test_completed_plus_lost_lte_injected(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=0)
        m = result.metrics
        assert m.items_completed + round(m.items_lost) <= m.items_injected

    def test_stage_counts_match_completed_items_tiny(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=0)
        m = result.metrics
        assert m.items_injected == 20
        assert m.items_completed == 20
        assert m.stage_metrics["scan_in"].items_processed == 20
        assert m.stage_metrics["wash"].items_processed == 20
        assert m.stage_metrics["separation"].items_processed == 20
        assert m.stage_metrics["delivery_scan"].items_processed == 20

    def test_downstream_not_exceed_upstream_completions(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=0)
        fc = result.flow.cumulative_completions
        assert fc.get("wash", 0) <= fc.get("scan_in", 0)
        assert fc.get("separation", 0) <= fc.get("wash", 0)
        assert fc.get("delivery_scan", 0) <= fc.get("final_qc", 0)


class TestWorkerCapacity:
    def test_scan_in_never_exceeds_worker_parallelism(self):
        from plant_sim.engine import PlantSimulation

        config = _load_fixture("tiny_plant.yaml")
        workers = config.stages.scan_in.worker_count()
        sim = PlantSimulation(config, ROOT, seed=0, track_flow=True)
        pool = sim._scan_resources["normal"]
        peak_in_service: list[int] = []

        def monitor():
            while True:
                peak_in_service.append(pool.count)
                yield sim.env.timeout(0.05)

        sim.env.process(monitor())
        result = sim.run()

        assert max(peak_in_service) <= pool.capacity
        assert pool.capacity == workers
        assert result.metrics.items_completed == 20


class TestUtilizationSanity:
    def test_utilization_bounded_zero_one(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=0)
        for sid, sm in result.metrics.stage_metrics.items():
            assert 0.0 <= sm.utilization <= 1.0, f"{sid} util={sm.utilization}"

    def test_not_all_stages_saturated_at_100_percent(self):
        config = _load_fixture("tiny_plant.yaml")
        result = _run_tracked(config, seed=0)
        utils = [sm.utilization for sm in result.metrics.stage_metrics.values()]
        assert not all(u > 0.99 for u in utils)


class TestReworkPath:
    def test_rework_respects_spotting_after_qc_failure(self):
        data = yaml.safe_load((FIXTURES / "tiny_plant.yaml").read_text(encoding="utf-8"))
        data["stages"]["final_qc"]["defect_rate"] = 1.0
        data["policies"]["qc_rework"]["max_cycles"] = 1
        config = PlantConfig.model_validate(data)
        result = _run_tracked(config, seed=0)
        assert result.flow.ok, result.flow.violations[:5]
        assert result.metrics.qc_rework_cycles >= 1
        assert result.metrics.stage_metrics["spotting"].items_processed >= 1


class TestWashCutoff:
    def test_late_arrival_defers_wash_past_cutoff(self):
        data = yaml.safe_load((FIXTURES / "tiny_plant.yaml").read_text(encoding="utf-8"))
        data["inputs"]["truck_schedule"] = "tests/fixtures/late_truck.csv"
        config = PlantConfig.model_validate(data)
        result = _run_tracked(config, seed=0)
        assert result.metrics.items_deferred_wash >= 1


class TestSteamRouting:
    def test_steam_direct_skips_press_but_reaches_qc(self):
        data = yaml.safe_load((FIXTURES / "routing_plant.yaml").read_text(encoding="utf-8"))
        data["routing"]["after_separation"]["pct_steam_tunnel"] = 100
        data["routing"]["after_separation"]["pct_spotting"] = 0
        data["routing"]["after_separation"]["pct_jacket_press"] = 0
        data["routing"]["after_separation"]["pct_general_press"] = 0
        data["routing"]["after_steam"]["pct_needs_press"] = 0
        config = PlantConfig.model_validate(data)
        result = _run_tracked(config, seed=10)
        assert result.flow.ok, result.flow.violations[:5]
        sm = result.metrics.stage_metrics
        assert sm["steam_tunnel"].items_processed >= 1
        assert sm["final_qc"].items_processed >= 1


class TestMonteCarloFlow:
    def test_randomized_routing_seeds(self):
        data = yaml.safe_load((FIXTURES / "routing_plant.yaml").read_text(encoding="utf-8"))
        config = PlantConfig.model_validate(data)
        rng = random.Random(99)
        for _ in range(20):
            seed = rng.randint(0, 10_000)
            result = _run_tracked(config, seed=seed)
            assert result.flow.ok, (
                f"seed={seed} violations={result.flow.violations[:3]}"
            )
