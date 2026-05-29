"""Snapshot diff/apply for streamed viz time series."""

from __future__ import annotations

import copy
from typing import Any


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def diff_snapshot(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, Any]:
    """Shallow-recursive diff: only keys whose values changed."""
    out: dict[str, Any] = {}
    keys = set(prev) | set(curr)
    for key in keys:
        pv = prev.get(key)
        cv = curr.get(key)
        if pv == cv:
            continue
        if _is_dict(pv) and _is_dict(cv):
            nested = diff_snapshot(pv, cv)
            if nested:
                out[key] = nested
        else:
            out[key] = copy.deepcopy(cv)
    return out


def apply_delta(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply recursive delta onto base; returns new dict."""
    result = copy.deepcopy(base)
    for key, val in delta.items():
        if key in result and _is_dict(result[key]) and _is_dict(val):
            result[key] = apply_delta(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def decode_step(prev_absolute: dict[str, Any] | None, step: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct absolute snapshot from previous state and one step entry."""
    mode = step.get("mode", "absolute")
    payload = step.get("snapshot") or {}
    if mode == "absolute" or prev_absolute is None:
        return copy.deepcopy(payload)
    return apply_delta(prev_absolute, payload)


def reconstruct_samples(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefix-reconstruct absolute samples from a batch step list."""
    samples: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for step in steps:
        abs_snap = decode_step(prev, step)
        samples.append({"t": step.get("t", 0.0), **abs_snap})
        prev = abs_snap
    return samples
