"""Unit tests for snapshot delta encoding (viz state reconstruction)."""

from __future__ import annotations

from plant_sim.snapshot_delta import apply_delta, diff_snapshot, reconstruct_samples


def test_apply_delta_merges_partial_nested_patch() -> None:
    """Regression: scalar-only washer deltas must not drop bin_fill / bin_capacity."""
    prev = {
        "washers": {
            "wash_1": {
                "bin_fill": 5,
                "bin_capacity": 10,
                "in_cycle": False,
                "cycle_progress": 0.0,
                "batch_size": 0,
                "queue_depth": 5,
            }
        }
    }
    delta = {
        "washers": {
            "wash_1": {
                "in_cycle": True,
                "cycle_progress": 0.5,
                "batch_size": 8,
                "queue_depth": 13,
            }
        }
    }
    restored = apply_delta(prev, delta)
    w = restored["washers"]["wash_1"]
    assert w["bin_fill"] == 5
    assert w["bin_capacity"] == 10
    assert w["in_cycle"] is True
    assert w["cycle_progress"] == 0.5
    assert w["batch_size"] == 8
    assert w["queue_depth"] == 13


def test_diff_apply_roundtrip() -> None:
    prev = {
        "zones": {"pre_scan_waiting": 10, "completed_waiting": 0},
        "items_completed": 5,
    }
    curr = {
        "zones": {"pre_scan_waiting": 12, "completed_waiting": 1},
        "items_completed": 6,
    }
    restored = apply_delta(prev, diff_snapshot(prev, curr))
    assert restored == curr


def test_reconstruct_absolute_then_delta_chain() -> None:
    steps = [
        {
            "i": 0,
            "t": 0.0,
            "mode": "absolute",
            "snapshot": {"zones": {"a": 1}, "items_completed": 0},
        },
        {
            "i": 1,
            "t": 1.0,
            "mode": "delta",
            "snapshot": {"zones": {"a": 2}, "items_completed": 1},
        },
    ]
    out = reconstruct_samples(steps)
    assert len(out) == 2
    assert out[0]["zones"]["a"] == 1
    assert out[1]["zones"]["a"] == 2
    assert out[1]["items_completed"] == 1
