"""Auto layout: pipeline groups (L→R) with vertical steps (↓) inside each stage."""

from __future__ import annotations

from typing import Any

from plant_sim.config_models import PlantConfig
from plant_sim.engine import SimulationResult
from plant_sim.unit_tracking import unit_label

HEADER = 0.15
HEADER_PX = 58
BLOCK_V_GAP = 0.022
BLOCK_H_PAD = 0.08
INNER_X = 0.07
INNER_W = 0.86
GAP_PX = 28
PAD_PX = 32
BASE_GROUP_W_PX = 160
WASHER_CELL_W_PX = 118
WASHER_CELL_H_PX = 122
WORKER_CELL_W_PX = 112
WORKER_CELL_H_PX = 66
MIN_BLOCK_REL_H = 0.14
BACKLOG_PANEL_PX = 92

# Vertical bands: main trunk, parallel finishing, tail (order preserved).
LAYOUT_BANDS: list[list[str]] = [
    ["inbound"],
    ["wash"],
    ["separation"],
    ["spotting", "steam", "jacket_press", "general_press"],
    ["final_qc"],
    ["delivery_scan", "outbound_scan"],
    ["outbound"],
]


def _washer_count(config: PlantConfig) -> int:
    return sum(w.count for w in config.resources.washers)


def _stage_count(config: PlantConfig, stage: str) -> int:
    stage_cfg = getattr(config.stages, stage, None)
    if stage_cfg is None or not stage_cfg.enabled:
        return 0
    return max(stage_cfg.worker_count(), 1)


def _chain_flow_next(blocks: list[dict[str, Any]]) -> None:
    for i in range(len(blocks) - 1):
        blocks[i]["flow_next"] = blocks[i + 1]["id"]


def _stack_block(
    bid: str,
    label: str,
    kind: str,
    index: int,
    total: int,
    *,
    rel_x: float | None = None,
    rel_w: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    gaps = BLOCK_V_GAP * max(total - 1, 0)
    band = (1.0 - HEADER - gaps) / max(total, 1)
    extra_copy = dict(extra)
    rel_h = min(float(extra_copy.pop("rel_h", band * 0.88)), band * 0.95)
    return {
        "id": bid,
        "label": label,
        "kind": kind,
        "pipeline_index": index,
        "rel_x": INNER_X if rel_x is None else rel_x,
        "rel_y": HEADER + index * (band + BLOCK_V_GAP),
        "rel_w": INNER_W if rel_w is None else rel_w,
        "rel_h": rel_h,
        **extra_copy,
    }


def _pool_worker_block(
    uid: str,
    label: str,
    pool_id: str,
    col: int,
    n_cols: int,
    row_y: float,
    row_h: float,
    **extra: Any,
) -> dict[str, Any]:
    cw = 0.9 / max(n_cols, 1)
    return {
        "id": uid,
        "label": label,
        "kind": "worker",
        "fifo_pool": pool_id,
        "uniform_fifo": True,
        "rel_x": INNER_X + col * cw,
        "rel_y": row_y,
        "rel_w": cw * 0.9,
        "rel_h": max(row_h * 0.88, MIN_BLOCK_REL_H),
        **extra,
    }


def _worker_grid(n_units: int, group_w_px: int) -> tuple[int, int]:
    """Columns/rows so each cell is at least WORKER_CELL_W_PX wide."""
    if n_units <= 0:
        return 1, 1
    max_cols = max(1, (group_w_px - 20) // WORKER_CELL_W_PX)
    cols = min(n_units, max_cols)
    rows = (n_units + cols - 1) // cols
    return cols, rows


def _worker_group_blocks(
    stage: str,
    n_workers: int,
    result: SimulationResult,
    sim_days: int,
    *,
    group_w_px: int,
    y0: float = HEADER,
    h_frac: float = 1.0 - HEADER,
) -> list[dict[str, Any]]:
    if n_workers <= 0:
        return []
    cols, rows = _worker_grid(n_workers, group_w_px)
    row_gap = BLOCK_V_GAP * max(rows - 1, 0)
    cw = 0.9 / cols
    ch = (h_frac - row_gap) / max(rows, 1)
    blocks: list[dict[str, Any]] = []
    for i in range(n_workers):
        uid = f"{stage}:{i}"
        um = result.metrics.unit_metrics.get(uid)
        block: dict[str, Any] = {
            "id": uid,
            "label": unit_label(stage, i),
            "kind": "worker",
            "fifo_pool": stage,
            "uniform_fifo": True,
            "rel_x": INNER_X + (i % cols) * cw,
            "rel_y": y0 + (i // cols) * (ch + BLOCK_V_GAP),
            "rel_w": cw * 0.9,
            "rel_h": ch * 0.88,
            "stage": stage,
            "items_processed": um.items_processed if um else 0,
            "daily_items": round((um.items_processed if um else 0) / sim_days, 1),
            "max_queue": round(um.max_queue, 1) if um else 0,
        }
        blocks.append(block)
    return blocks


def _group_width_px(gid: str, config: PlantConfig, blocks: list[dict[str, Any]]) -> int:
    if gid == "wash":
        n = max(_washer_count(config), 1)
        cols = min(n, 4)
        return max(BASE_GROUP_W_PX, cols * WASHER_CELL_W_PX + 28)
    workers = sum(1 for b in blocks if b.get("kind") == "worker")
    if workers > 0:
        cols, _ = _worker_grid(workers, BASE_GROUP_W_PX * 3)
        return max(BASE_GROUP_W_PX, cols * WORKER_CELL_W_PX + 28)
    return BASE_GROUP_W_PX


def _group_height_px(
    gid: str, config: PlantConfig, blocks: list[dict[str, Any]], width_px: int
) -> int:
    washers = sum(1 for b in blocks if b.get("kind") == "washer")
    if washers > 0:
        cols = min(washers, max(1, (width_px - 24) // WASHER_CELL_W_PX))
        rows = (washers + cols - 1) // cols
        return HEADER_PX + rows * WASHER_CELL_H_PX + 32
    workers = sum(1 for b in blocks if b.get("kind") == "worker")
    if workers > 0:
        _, rows = _worker_grid(workers, width_px)
        return HEADER_PX + rows * WORKER_CELL_H_PX + 28
    stacked = max(
        len([b for b in blocks if b.get("pipeline_index") is not None]),
        1,
    )
    return HEADER_PX + stacked * 76 + int(BLOCK_V_GAP * 1000) * max(stacked - 1, 0) + 20


def _layout_bands(groups: list[dict[str, Any]]) -> list[list[str]]:
    by_id = {g["id"]: g for g in groups}
    bands: list[list[str]] = []
    placed: set[str] = set()
    for band_ids in LAYOUT_BANDS:
        row = [gid for gid in band_ids if gid in by_id]
        if row:
            bands.append(row)
            placed.update(row)
    for g in groups:
        if g["id"] not in placed:
            bands.append([g["id"]])
    return bands


def _finalize_graph_layout(groups: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Top-to-bottom bands; each row and the full diagram centered horizontally."""
    by_id = {g["id"]: g for g in groups}
    rows: list[tuple[list[dict[str, Any]], float, float]] = []
    max_row_w = 0.0
    for row_ids in _layout_bands(groups):
        row = [by_id[gid] for gid in row_ids]
        row_w = sum(g["width_px"] for g in row) + GAP_PX * max(len(row) - 1, 0)
        row_h = max(g["height_px"] for g in row)
        max_row_w = max(max_row_w, row_w)
        rows.append((row, row_w, row_h))

    canvas_w = int(max_row_w + 2 * PAD_PX)
    center_x = canvas_w / 2
    y = float(PAD_PX)
    for row, row_w, row_h in rows:
        x = center_x - row_w / 2
        for g in row:
            g["x_px"] = int(round(x))
            g["y_px"] = int(round(y))
            x += g["width_px"] + GAP_PX
        y += row_h + GAP_PX

    canvas_h = int(y + PAD_PX)
    pipeline_content_w = int(max_row_w)
    for g in groups:
        g["layout_center_x"] = int(round(center_x))

    return canvas_w, canvas_h, pipeline_content_w


def build_group_layout(
    config: PlantConfig, result: SimulationResult
) -> dict[str, Any]:
    sim_days = max(config.objectives.simulation_days, 1)
    r = config.routing.after_separation
    groups: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    pipeline_order = 0

    def add_group(
        gid: str,
        label: str,
        schema: str,
        blocks: list[dict[str, Any]],
        *,
        note: str = "",
        flow_axis: str = "vertical",
        distribution: str | None = None,
        width_px: int | None = None,
        group_backlog: dict[str, Any] | None = None,
    ) -> None:
        nonlocal pipeline_order
        if distribution is None and schema == "fifo":
            distribution = "uniform"
        body_w = width_px or _group_width_px(gid, config, blocks)
        panel_px = BACKLOG_PANEL_PX if group_backlog else 0
        wpx = body_w + panel_px
        hpx = _group_height_px(gid, config, blocks, body_w)
        entry: dict[str, Any] = {
            "id": gid,
            "label": label,
            "schema": schema,
            "schema_note": note,
            "distribution": distribution,
            "flow_axis": flow_axis,
            "pipeline_order": pipeline_order,
            "width_px": wpx,
            "body_width_px": body_w,
            "backlog_panel_px": panel_px,
            "height_px": hpx,
            "blocks": blocks,
        }
        if group_backlog:
            entry["group_backlog"] = group_backlog
        groups.append(entry)
        pipeline_order += 1

    # --- Inbound ---
    n_in_steps = 2 if config.stages.scan_in.enabled else 3
    inbound_steps: list[dict[str, Any]] = [
        _stack_block("truck_in", "Trucks in", "source", 0, n_in_steps),
        _stack_block("pre_scan_waiting", "Inbound wait", "buffer_fifo", 1, n_in_steps),
    ]
    scan_blocks: list[dict[str, Any]] = []
    if config.stages.scan_in.enabled:
        n_scan = _stage_count(config, "scan_in")
        cols = min(n_scan, 4)
        pool_y = HEADER + (1.0 - HEADER) * 0.38
        pool_h = (1.0 - HEADER) * 0.22
        for i in range(n_scan):
            scan_blocks.append(
                _pool_worker_block(
                    f"scan_in:{i}",
                    f"Scan {i + 1}",
                    "scan_in",
                    i % cols,
                    cols,
                    pool_y,
                    pool_h,
                    stage="scan_in",
                )
            )
        inbound_steps[0]["flow_next"] = "pre_scan_waiting"
        inbound_steps[1]["flow_next"] = scan_blocks[0]["id"]
        _chain_flow_next(scan_blocks)
    else:
        inbound_steps.append(
            {
                "id": "scan_bypass",
                "label": "Scan bypassed",
                "kind": "stage",
                "pipeline_index": 2,
                "rel_x": INNER_X,
                "rel_y": HEADER + (1.0 - HEADER) * 0.38,
                "rel_w": INNER_W,
                "rel_h": (1.0 - HEADER) * 0.1,
            }
        )
        _chain_flow_next(inbound_steps)
    inbound_blocks = inbound_steps + scan_blocks
    add_group(
        "inbound",
        "Inbound",
        "fifo",
        inbound_blocks,
        note="Trucks → scan → wash reserve",
        width_px=160,
        group_backlog={
            "lines": ["INBOUND", "BACKLOG"],
            "accent": "#70a1ff",
            "metric": "zone:inbound_backlog",
            "anchor_id": "pre_scan_waiting",
        },
    )

    # --- Wash ---
    washer_blocks: list[dict[str, Any]] = []
    n_wash = max(_washer_count(config), 1)
    wash_w = _group_width_px("wash", config, [])
    cols, wash_rows = _worker_grid(n_wash, wash_w)
    row_gap = BLOCK_V_GAP * max(wash_rows - 1, 0)
    cw = 0.9 / cols
    ch = (1.0 - HEADER - row_gap) / max(wash_rows, 1)
    idx = 0
    for wdef in config.resources.washers:
        for i in range(wdef.count):
            wid = f"{wdef.id}:{i}"
            um = result.metrics.unit_metrics.get(wid)
            washer_blocks.append(
                {
                    "id": wid,
                    "label": f"{wdef.id.replace('_', ' ')} {i + 1}",
                    "kind": "washer",
                    "rel_x": INNER_X + (idx % cols) * cw,
                    "rel_y": HEADER + (idx // cols) * (ch + BLOCK_V_GAP),
                    "rel_w": cw * 0.9,
                    "rel_h": ch * 0.88,
                    "capacity_items": wdef.capacity_items,
                    "cycle_minutes": wdef.cycle_minutes,
                    "max_queue": round(result.metrics.washer_max_queue.get(wid, 0), 1),
                    "items_processed": um.items_processed if um else 0,
                    "daily_items": round((um.items_processed if um else 0) / sim_days, 1),
                }
            )
            idx += 1
    add_group(
        "wash",
        "Wash",
        "batch",
        washer_blocks,
        note="Fill-first batch lines → separation",
        flow_axis="horizontal_grid",
        distribution=None,
        group_backlog={
            "lines": ["WASH", "BACKLOG"],
            "accent": "#4a9eff",
            "metric": "zone:post_scan_waiting",
            "anchor_id": "post_scan_waiting",
        },
    )
    links.append(
        {"from": "inbound", "to": "wash", "schema": "fifo", "distribution": "uniform"}
    )

    # --- Separation ---
    n_sep = _stage_count(config, "separation")
    sep_w = max(190, _worker_grid(n_sep, 200)[0] * WORKER_CELL_W_PX + 52)
    sep_workers = _worker_group_blocks(
        "separation",
        n_sep,
        result,
        sim_days,
        group_w_px=sep_w,
        y0=HEADER,
        h_frac=1.0 - HEADER - 0.02,
    )
    if sep_workers:
        _chain_flow_next(sep_workers)
    add_group(
        "separation",
        "Separation",
        "fifo",
        sep_workers,
        note="Batch queue → separators",
        width_px=sep_w,
        group_backlog={
            "lines": ["SEPARATION", "BACKLOG"],
            "accent": "#a29bfe",
            "metric": "zone:separation_backlog",
            "anchor_id": "separation_backlog",
        },
    )
    links.append({"from": "wash", "to": "separation", "schema": "batch"})

    # --- Stage-type finishing groups ---
    branches: list[tuple[str, str, int, float]] = [
        ("spotting", "Spotting", _stage_count(config, "spotting"), r.pct_spotting),
        ("steam_tunnel", "Steam", 1, r.pct_steam_tunnel),
        ("jacket_press", "Jacket press", _stage_count(config, "jacket_press"), r.pct_jacket_press),
        ("general_press", "General press", _stage_count(config, "general_press"), r.general_press_pct()),
    ]

    if r.pct_spotting > 0 and _stage_count(config, "spotting") > 0:
        n_spot = _stage_count(config, "spotting")
        spot_w = max(BASE_GROUP_W_PX, _worker_grid(n_spot, 220)[0] * WORKER_CELL_W_PX + 52)
        spot_blocks = _worker_group_blocks(
            "spotting", n_spot, result, sim_days, group_w_px=spot_w
        )
        add_group(
            "spotting",
            "Spotting",
            "fifo",
            spot_blocks,
            note="Separation & QC rework",
            width_px=spot_w,
            group_backlog={
                "lines": ["SPOTTING", "BACKLOG"],
                "accent": "#ffa502",
                "metric": "pool_sum:spotting",
            },
        )
        links.append(
            {
                "from": "separation",
                "to": "spotting",
                "schema": "split",
                "distribution": "weighted",
                "note": f"{r.pct_spotting:.0f}%",
            }
        )

    if r.pct_steam_tunnel > 0 and config.stages.steam_tunnel.enabled:
        steam_blocks = [
            _stack_block("steam_tunnel", "Steam tunnel", "stage", 0, 2),
            _stack_block("steam_exit_check", "Steam check", "stage", 1, 2),
        ]
        _chain_flow_next(steam_blocks)
        add_group(
            "steam",
            "Steam",
            "fifo",
            steam_blocks,
            note="Tunnel → check",
            width_px=140,
        )
        links.append(
            {
                "from": "separation",
                "to": "steam",
                "schema": "split",
                "distribution": "weighted",
                "note": f"{r.pct_steam_tunnel:.0f}%",
            }
        )

    if r.pct_jacket_press > 0 and _stage_count(config, "jacket_press") > 0:
        n_jacket = _stage_count(config, "jacket_press")
        jacket_w = max(BASE_GROUP_W_PX, _worker_grid(n_jacket, 220)[0] * WORKER_CELL_W_PX + 28)
        jacket_blocks = _worker_group_blocks(
            "jacket_press", n_jacket, result, sim_days, group_w_px=jacket_w
        )
        add_group(
            "jacket_press",
            "Jacket press",
            "fifo",
            jacket_blocks,
            note="From separation",
            width_px=jacket_w,
        )
        links.append(
            {
                "from": "separation",
                "to": "jacket_press",
                "schema": "split",
                "distribution": "weighted",
                "note": f"{r.pct_jacket_press:.0f}%",
            }
        )

    n_press = _stage_count(config, "general_press")
    if r.general_press_pct() > 0 and n_press > 0:
        press_w = max(
            BASE_GROUP_W_PX,
            _worker_grid(n_press, 240)[0] * WORKER_CELL_W_PX + 52,
        )
        press_workers = _worker_group_blocks(
            "general_press",
            n_press,
            result,
            sim_days,
            group_w_px=press_w,
            y0=HEADER,
            h_frac=1.0 - HEADER - 0.02,
        )
        if press_workers:
            _chain_flow_next(press_workers)
        add_group(
            "general_press",
            "General press",
            "fifo",
            press_workers,
            note="Line + pressers",
            width_px=press_w,
            group_backlog={
                "lines": ["PRESS LINE", "BACKLOG"],
                "accent": "#ffb142",
                "metric": "pool_sum:general_press",
                "anchor_id": "press_conveyor",
            },
        )
        links.append(
            {
                "from": "separation",
                "to": "general_press",
                "schema": "split",
                "distribution": "weighted",
                "note": f"{r.general_press_pct():.0f}%",
            }
        )
        if any(g["id"] == "spotting" for g in groups):
            links.append(
                {
                    "from": "spotting",
                    "to": "general_press",
                    "schema": "fifo",
                    "distribution": "uniform",
                    "note": "→ press line",
                }
            )
        if any(g["id"] == "steam" for g in groups):
            links.append(
                {
                    "from": "steam",
                    "to": "general_press",
                    "schema": "fifo",
                    "distribution": "uniform",
                    "note": "re-press path",
                }
            )

    # --- QC + delivery (outbound prep) ---
    qc_blocks = [_stack_block("final_qc", "Final QC", "stage", 0, 1)]
    add_group(
        "final_qc",
        "Final QC",
        "fifo",
        qc_blocks,
        note="Rework → spotting",
        width_px=150,
        group_backlog={
            "lines": ["QC", "BACKLOG"],
            "accent": "#c8b6ff",
            "metric": "stage:final_qc",
        },
    )
    if any(g["id"] == "general_press" for g in groups):
        links.append(
            {"from": "general_press", "to": "final_qc", "schema": "fifo", "distribution": "uniform"}
        )
    if any(g["id"] == "jacket_press" for g in groups):
        links.append(
            {"from": "jacket_press", "to": "final_qc", "schema": "fifo", "distribution": "uniform"}
        )
    if any(g["id"] == "steam" for g in groups):
        links.append(
            {
                "from": "steam",
                "to": "final_qc",
                "schema": "fifo",
                "distribution": "uniform",
                "note": "direct after steam",
            }
        )
    if any(g["id"] == "spotting" for g in groups):
        links.append(
            {
                "from": "final_qc",
                "to": "spotting",
                "schema": "fifo",
                "distribution": "uniform",
                "note": "QC rework",
                "link_kind": "rework",
            }
        )

    delivery_blocks = [_stack_block("delivery_scan", "Delivery scan", "stage", 0, 1)]
    add_group(
        "delivery_scan",
        "Delivery scan",
        "fifo",
        delivery_blocks,
        width_px=155,
        group_backlog={
            "lines": ["DELIVERY", "BACKLOG"],
            "accent": "#55efc4",
            "metric": "stage:delivery_scan",
        },
    )
    links.append(
        {"from": "final_qc", "to": "delivery_scan", "schema": "fifo", "distribution": "uniform"}
    )

    if config.stages.outbound_scan.enabled:
        outbound_scan_blocks = [
            _stack_block("outbound_scan", "Outbound scan", "stage", 0, 1)
        ]
        add_group(
            "outbound_scan",
            "Outbound scan",
            "fifo",
            outbound_scan_blocks,
            width_px=155,
            group_backlog={
                "lines": ["OUTBOUND", "SCAN"],
                "accent": "#ffeaa7",
                "metric": "stage:outbound_scan",
            },
        )
        links.append(
            {
                "from": "delivery_scan",
                "to": "outbound_scan",
                "schema": "fifo",
                "distribution": "uniform",
            }
        )

    out_blocks = [_stack_block("truck_out", "Trucks out", "sink", 0, 1)]
    add_group(
        "outbound",
        "Outbound",
        "fifo",
        out_blocks,
        note="Trucks out",
        width_px=130,
        group_backlog={
            "lines": ["READY", "TO SHIP"],
            "accent": "#dfe6e9",
            "metric": "zone:completed_waiting",
        },
    )
    outbound_from = (
        "outbound_scan"
        if config.stages.outbound_scan.enabled
        else "delivery_scan"
    )
    links.append(
        {
            "from": outbound_from,
            "to": "outbound",
            "schema": "fifo",
            "distribution": "uniform",
        }
    )

    canvas_w, canvas_h, pipeline_w = _finalize_graph_layout(groups)
    layout_meta = {
        "version": 3,
        "group_flow": "vertical",
        "block_flow": "vertical",
        "layout_bands": _layout_bands(groups),
        "editable": True,
        "canvas_width_px": canvas_w,
        "canvas_height_px": canvas_h,
        "pipeline_content_width_px": pipeline_w,
        "layout_center_x": canvas_w / 2,
        "notes": "Each layer centered on pipeline width; backlog panels attached left.",
    }

    return {
        "groups": groups,
        "group_links": links,
        "layout_meta": layout_meta,
    }
