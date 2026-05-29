/**
 * Group-based plant flow map — scrollable pipeline, playback timeline.
 */

const canvas = document.getElementById("flow-canvas");
const ctx = canvas.getContext("2d");
const statusEl = document.getElementById("status");
const kpiEl = document.getElementById("kpi");
const bottlenecksEl = document.getElementById("bottlenecks");
const outboundKpiEl = document.getElementById("outbound-kpi");
const modelAssumptionsEl = document.getElementById("model-assumptions");
const errorEl = document.getElementById("error");
const timeLabelEl = document.getElementById("time-label");
const clockEl = document.getElementById("sim-clock");
const legendClockEl = document.getElementById("legend-clock");
const timelineEl = document.getElementById("inp-timeline");
const unitPanel = document.getElementById("unit-panel");
const unitDetailEl = document.getElementById("unit-detail");
const simProgressWrap = document.getElementById("sim-progress-wrap");
const simProgressEl = document.getElementById("sim-progress");
const simProgressLabel = document.getElementById("sim-progress-label");
const btnRefresh = document.getElementById("btn-refresh");
const btnWeek = document.getElementById("btn-week");

let state = {
  groups: [],
  group_links: [],
  layout_meta: {},
  flow_events: [],
  time_series: null,
  summary: {},
  block_statistics: {},
};
let blockMap = new Map();
let groupMap = new Map();
let blockToGroup = new Map();
let particles = [];
let timelineIndex = 0;
let playing = false;
let selectedBlockId = null;
/** Sim minutes advanced per real second (1 = one sim minute per second at 1 min snapshots). */
let playbackSpeed = 1;
/** Index into time_series.samples — playback and scrub share this. */
let playbackSampleIndex = 0;
let lastFrame = performance.now();
let eventCursor = 0;
let playbackStepDebt = 0;
let streamRecording = null;
let streamEventSource = null;
let currentStreamJobId = null;
let configBranchTimer = null;
const MAX_PARTICLES = 400;
const MIN_BLOCK_W = 88;
const MIN_BLOCK_H = 48;
const LAYOUT_STORE_KEY = "plantFlowGroupOffsets";
const SIM_MEMO_INDEX_KEY = "viz_sim_memo_index";
const SIM_MEMO_PREFIX = "viz_sim_memo_";
const MAX_SIM_MEMO_ENTRIES = 6;
const BRANCH_FIDELITY_NOTE =
  "Branch restore replays aggregate queue/washer state; individual item timing after a fork is approximate.";
const view = { x: 0, y: 0, scale: 1 };
let pointer = null;

const F = { xs: 11, sm: 13, md: 15, lg: 17, xl: 20, count: 18, hero: 28, badge: 12 };
const BACKLOG_PANEL_DEFAULT = 92;
const BACKLOG_COUNT_AREA = 52;
const BACKLOG_ANCHOR_NODES = {
  separation_backlog: "separation",
  completed_goods: "outbound",
  press_conveyor: "general_press",
  post_scan_waiting: "wash",
  pre_scan_waiting: "inbound",
};
const SCHEMA_COLORS = {
  fifo: "#7bed9f",
  batch: "#a29bfe",
  split: "#70a1ff",
  default: "#9aa0a6",
};

/** roundRect polyfill + safe radii (avoid array form — breaks some Canvas implementations). */
function installRoundRectPolyfill() {
  if (CanvasRenderingContext2D.prototype.roundRect) return;
  CanvasRenderingContext2D.prototype.roundRect = function roundRectPoly(x, y, w, h, r) {
    const rad = typeof r === "number" && r > 0 ? Math.min(r, w / 2, h / 2) : 0;
    if (rad <= 0) {
      this.rect(x, y, w, h);
      return;
    }
    this.moveTo(x + rad, y);
    this.arcTo(x + w, y, x + w, y + h, rad);
    this.arcTo(x + w, y + h, x, y + h, rad);
    this.arcTo(x, y + h, x, y, rad);
    this.arcTo(x, y, x + w, y, rad);
    this.closePath();
  };
}

function safeRoundRect(x, y, w, h, r = 6) {
  const rad = typeof r === "number" && r > 0 ? Math.min(r, w / 2, h / 2) : 0;
  ctx.beginPath();
  if (rad <= 0) {
    ctx.rect(x, y, w, h);
    return;
  }
  ctx.roundRect(x, y, w, h, rad);
}

installRoundRectPolyfill();

function setFont(size, weight = "") {
  ctx.font = weight ? `${weight} ${size}px system-ui` : `${size}px system-ui`;
}

function formatBacklogCount(count) {
  const v = Math.round(count);
  if (v >= 10000) return `${(v / 1000).toFixed(0)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function backlogCountColor(count, accent) {
  if (count > 120) return "#ff6b6b";
  if (count > 50) return "#ffa502";
  return accent || "#e8eaed";
}

function poolSumQueue(stageOrPool) {
  const peers = [...blockMap.values()].filter(
    (b) => b.stage === stageOrPool || b.fifo_pool === stageOrPool
  );
  return peers.reduce((s, p) => s + (unitQueue(p.id) || 0), 0);
}

function backlogCountForGroup(g) {
  const bl = g.group_backlog;
  if (!bl?.metric) return 0;
  const m = bl.metric;
  if (m.startsWith("zone:")) return zoneCount(m.slice(5));
  if (m.startsWith("pool_sum:")) return poolSumQueue(m.slice(9));
  if (m.startsWith("stage:")) {
    const st = activeSample()?.stages?.[m.slice(6)];
    return st?.waiting ?? st?.queue_depth ?? 0;
  }
  return 0;
}

function fitRotatedBacklogLabel(label, maxLen) {
  let size = 14;
  const minSize = 9;
  while (size >= minSize) {
    setFont(size, "bold");
    if (ctx.measureText(label).width <= maxLen) return { size, text: label };
    size -= 1;
  }
  setFont(minSize, "bold");
  let t = label;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > maxLen) t = t.slice(0, -1);
  return { size: minSize, text: `${t}…` };
}

/** External backlog panel joined to the left of the main group body. */
function drawBacklogPanel(g) {
  if (!g.group_backlog || !g._backlog_pw) return;
  const badge = g.group_backlog;
  const accent = badge.accent || "#8fd694";
  const pw = g._backlog_pw;
  const ph = g._ph;
  const count = backlogCountForGroup(g);

  ctx.fillStyle = "rgba(10, 14, 20, 0.97)";
  safeRoundRect(g._px, g._py, pw, ph, 8);
  ctx.fill();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2.5;
  ctx.stroke();

  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  setFont(F.hero, "bold");
  ctx.fillStyle = backlogCountColor(count, accent);
  ctx.fillText(formatBacklogCount(count), g._px + pw / 2, g._py + 28);

  const label = (badge.lines || ["BACKLOG"]).join(" ");
  const maxLabelLen = Math.max(24, ph - BACKLOG_COUNT_AREA - 16);
  const { size, text } = fitRotatedBacklogLabel(label, maxLabelLen);
  ctx.save();
  ctx.translate(g._px + pw / 2, g._py + BACKLOG_COUNT_AREA + maxLabelLen / 2);
  ctx.rotate(-Math.PI / 2);
  setFont(size, "bold");
  ctx.fillStyle = accent;
  ctx.fillText(text, 0, 0);
  ctx.restore();
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
}

function backlogAnchorBlock(nodeId) {
  const gid = BACKLOG_ANCHOR_NODES[nodeId];
  if (!gid) return null;
  const g = groupMap.get(gid);
  if (!g?.group_backlog || !g._backlog_pw) return null;
  return {
    id: nodeId,
    _px: g._px,
    _py: g._py,
    _pw: g._backlog_pw,
    _ph: g._ph,
    _anchor: true,
  };
}

function fitLabel(text, maxW, size = F.sm, weight = "bold") {
  if (!text) return "";
  setFont(size, weight);
  if (ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > maxW) t = t.slice(0, -1);
  return `${t}…`;
}

function loadGroupOffsets() {
  try {
    return JSON.parse(localStorage.getItem(LAYOUT_STORE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveGroupOffsets(all) {
  localStorage.setItem(LAYOUT_STORE_KEY, JSON.stringify(all));
}

function canvasPointFromEvent(ev) {
  const rect = canvas.getBoundingClientRect();
  return { sx: ev.clientX - rect.left, sy: ev.clientY - rect.top };
}

function screenToWorld(sx, sy) {
  return { x: (sx - view.x) / view.scale, y: (sy - view.y) / view.scale };
}

function worldToScreen(wx, wy) {
  return { x: wx * view.scale + view.x, y: wy * view.scale + view.y };
}

function resize() {
  const wrap = document.getElementById("canvas-wrap");
  const w = Math.max(wrap.clientWidth, 320);
  const h = Math.max(wrap.clientHeight, 400);
  canvas.width = w;
  canvas.height = h;
}

function fitViewToLayout() {
  const wrap = document.getElementById("canvas-wrap");
  const meta = state.layout_meta || {};
  const pad = 48;
  let minX = 0;
  let minY = 0;
  let maxX = meta.canvas_width_px || 900;
  let maxY = meta.canvas_height_px || 600;

  if ((state.groups || []).length) {
    let gMinX = Infinity;
    let gMinY = Infinity;
    let gMaxX = 0;
    let gMaxY = 0;
    for (const g of state.groups) {
      gMinX = Math.min(gMinX, g._px);
      gMinY = Math.min(gMinY, g._py);
      gMaxX = Math.max(gMaxX, g._px + g._pw);
      gMaxY = Math.max(gMaxY, g._py + g._ph);
    }
    if (meta.canvas_width_px > 0 && meta.canvas_height_px > 0) {
      minX = 0;
      minY = 0;
      maxX = meta.canvas_width_px;
      maxY = meta.canvas_height_px;
    } else if (Number.isFinite(gMinX)) {
      minX = gMinX - pad;
      maxX = gMaxX + pad;
      minY = gMinY - pad;
      maxY = gMaxY + pad;
    }
  }

  const lw = Math.max(maxX - minX, 1);
  const lh = Math.max(maxY - minY, 1);
  const cw = Math.max(wrap.clientWidth, 320);
  const ch = Math.max(wrap.clientHeight, 400);
  view.scale = Math.min(cw / lw, ch / lh, 1);
  if (!Number.isFinite(view.scale) || view.scale <= 0) view.scale = 1;
  view.scale = Math.max(0.25, view.scale);
  view.x = (cw - lw * view.scale) / 2 - minX * view.scale;
  view.y = (ch - lh * view.scale) / 2 - minY * view.scale;
}

function applyStoredGroupOffsets() {
  const offsets = loadGroupOffsets();
  for (const g of state.groups || []) {
    const o = offsets[g.id] || {};
    g._ux = o.dx || 0;
    g._uy = o.dy || 0;
  }
}

function layoutAll(reloadOffsets = false) {
  blockMap.clear();
  groupMap.clear();
  blockToGroup.clear();
  if (reloadOffsets) {
    applyStoredGroupOffsets();
  }

  for (const g of state.groups || []) {
    if (g._ux == null) g._ux = 0;
    if (g._uy == null) g._uy = 0;
    g._px = (g.x_px ?? 0) + g._ux;
    g._py = (g.y_px ?? 36) + g._uy;
    g._pw = Math.max(g.width_px ?? 150, 120);
    g._ph = Math.max(g.height_px ?? 200, 120);
    g._backlog_pw = g.group_backlog
      ? g.backlog_panel_px || BACKLOG_PANEL_DEFAULT
      : 0;
    g._body_px = g._px + g._backlog_pw;
    g._body_pw = Math.max(g._pw - g._backlog_pw, g.body_width_px || g._pw, MIN_BLOCK_W);
    groupMap.set(g.id, g);
    for (const b of g.blocks || []) {
      b._px = g._body_px + b.rel_x * g._body_pw;
      b._py = g._py + b.rel_y * g._ph;
      b._pw = Math.max(b.rel_w * g._body_pw, MIN_BLOCK_W);
      b._ph = Math.max(b.rel_h * g._ph, MIN_BLOCK_H);
      b._groupId = g.id;
      blockMap.set(b.id, b);
      blockToGroup.set(b.id, g.id);
    }
  }
}

function groupAt(wx, wy) {
  const groups = [...(state.groups || [])].sort(
    (a, b) => b._py + b._ph - (a._py + a._ph)
  );
  for (const g of groups) {
    const headerH = g._headerH || Math.min(48, Math.max(40, g._ph * 0.14));
    if (
      wx >= g._px &&
      wx <= g._px + g._pw &&
      wy >= g._py &&
      wy <= g._py + headerH
    ) {
      return g;
    }
  }
  return null;
}

function blockAt(wx, wy) {
  for (const b of blockMap.values()) {
    if (wx >= b._px && wx <= b._px + b._pw && wy >= b._py && wy <= b._py + b._ph) {
      return b;
    }
  }
  return null;
}

function blockCenter(b) {
  return { x: b._px + b._pw / 2, y: b._py + b._ph / 2 };
}

function resolveBlock(id) {
  if (!id) return null;
  const anchor = backlogAnchorBlock(id);
  if (anchor) return anchor;
  if (blockMap.has(id)) return blockMap.get(id);
  const base = id.replace(/:wait$|:press$|:bin$/, "");
  if (blockMap.has(base)) return blockMap.get(base);
  if (base.includes(":")) {
    const uid = base.split(":").slice(0, 2).join(":");
    if (blockMap.has(uid)) return blockMap.get(uid);
  }
  for (const [bid, b] of blockMap) {
    if (bid.startsWith(base + ":")) return b;
  }
  if (!base.includes(":")) {
    for (const [bid, b] of blockMap) {
      if (bid.startsWith(base + ":")) return b;
    }
  }
  return null;
}

function zoneCount(key) {
  return activeSample()?.zones?.[key] ?? 0;
}

function scanWorkerQueue(blockId) {
  const sw = activeSample()?.zones?.scan_workers;
  if (!sw) return 0;
  const row = sw[blockId];
  return row?.wait ?? row?.queue_depth ?? 0;
}

function washerState(id) {
  return activeSample()?.washers?.[id] || {};
}

function unitQueue(id) {
  const sample = activeSample();
  if (!sample?.units) return 0;
  const u = sample.units[id];
  return u?.waiting ?? u?.queue_depth ?? 0;
}

function samples() {
  return state.time_series?.samples || [];
}

function playbackStartMinutes() {
  const ts = state.time_series;
  if (ts?.playback_start_minutes != null && ts.playback_start_minutes >= 0) {
    return ts.playback_start_minutes;
  }
  if (state.summary?.playback_start_minutes != null) {
    return state.summary.playback_start_minutes;
  }
  const s = samples();
  return s.length ? s[0].t : 0;
}

function playbackHorizonMinutes() {
  const ts = state.time_series;
  if (ts?.playback_horizon_minutes > 0) return ts.playback_horizon_minutes;
  if (state.summary?.playback_horizon_minutes > 0) return state.summary.playback_horizon_minutes;
  const s = samples();
  return s.length ? s[s.length - 1].t : 0;
}

function playbackWindowMinutes() {
  const ts = state.time_series;
  if (ts?.playback_window_minutes > 0) return ts.playback_window_minutes;
  return Math.max(0, playbackHorizonMinutes() - playbackStartMinutes());
}

function operatingDaysSimulated() {
  return state.config_snapshot?.simulation_days ?? 1;
}

function validPlaybackMaxIndex() {
  if (streamRecording) {
    const v = streamRecording.validMaxIndex();
    if (v >= 0) return v;
  }
  const s = samples();
  return s.length ? s.length - 1 : 0;
}

function currentSampleIndex() {
  const s = samples();
  if (!s.length) return 0;
  const maxI = validPlaybackMaxIndex();
  const idx = playing ? playbackSampleIndex : timelineIndex;
  return Math.min(Math.max(0, idx), maxI);
}

function activeSample() {
  const s = samples();
  if (!s.length) return null;
  return s[currentSampleIndex()];
}

function formatWallClock(sample) {
  const c = sample?.clock;
  if (!c) return null;
  let label = `${c.weekday} ${c.time_of_day}`;
  if (c.calendar_day > 1) label += ` · day ${c.calendar_day}`;
  return label;
}

function syncClockDisplays() {
  const wall = formatWallClock(activeSample()) || "—";
  if (clockEl) clockEl.textContent = wall;
  if (legendClockEl) legendClockEl.textContent = wall;
}

function snapshotIntervalMinutes() {
  const fromSeries = state.time_series?.interval_minutes;
  if (fromSeries != null && fromSeries > 0) return fromSeries;
  return getSnapshotIntervalMinutes();
}

function readPlaybackSettings() {
  playbackSpeed = Math.max(
    0.1,
    parseFloat(document.getElementById("inp-playback-speed").value) || 1
  );
}

/** Snapshots advanced per real second at current speed & interval. */
function snapshotsPerSecond() {
  return playbackSpeed / snapshotIntervalMinutes();
}

function syncEventCursorToTime(t) {
  const events = state.flow_events || [];
  let c = 0;
  while (c < events.length && events[c].t <= t) c++;
  eventCursor = c;
}

function updateOutboundKpiFromSample() {
  const el = document.getElementById("kpi-completed-waiting");
  if (!el) return;
  const waiting = activeSample()?.zones?.completed_waiting;
  el.textContent = waiting != null ? String(Math.round(waiting)) : "—";
}

function updateTimeLabel() {
  syncClockDisplays();
  updateOutboundKpiFromSample();
  const s = samples();
  const idx = currentSampleIndex();
  const wall = formatWallClock(activeSample());
  if (wall && s.length) {
    const speedHint = playing ? ` · ${playbackSpeed} sim min/s` : "";
    timeLabelEl.textContent = `${wall} · ${idx + 1}/${s.length}${speedHint}`;
    return;
  }
  timeLabelEl.textContent = "—";
}

function formatTimelineTitle() {
  const s = samples();
  if (!s.length) return "";
  const interval = snapshotIntervalMinutes();
  const days = operatingDaysSimulated();
  const winH = (playbackWindowMinutes() / 60).toFixed(1);
  const dayLabel = days === 1 ? "1 operating day" : `${days} operating days`;
  return (
    `${s.length} snapshots · ${interval} sim min each · ${dayLabel} ` +
    `(${winH} h operating window) · scrub/play only within open→cutoff`
  );
}

function setupTimeline() {
  const s = samples();
  const maxIdx = Math.max(0, validPlaybackMaxIndex());
  timelineEl.disabled = s.length < 2;
  const hasData = s.length >= 1;
  document.getElementById("btn-play").disabled = !hasData;
  document.getElementById("btn-live").disabled = s.length < 2;
  const exportBtn = document.getElementById("btn-export-recording");
  if (exportBtn) exportBtn.disabled = !hasData;
  if (s.length) {
    timelineEl.min = "0";
    timelineEl.max = String(maxIdx);
    timelineEl.step = "1";
    playbackSampleIndex = Math.min(playbackSampleIndex, maxIdx);
    timelineIndex = Math.min(timelineIndex, maxIdx);
    timelineEl.value = String(timelineIndex);
    timeLabelEl.title = formatTimelineTitle();
  }
  syncEventCursorToTime(s[0]?.t ?? playbackStartMinutes());
  updateTimeLabel();
}

function drawGarmentDot(x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function drawLinedQueue(x, y, w, h, count, maxDots = 24, color = "#70a1ff") {
  const n = Math.min(Math.max(0, Math.round(count)), maxDots);
  if (n === 0) return;
  const dotR = Math.min(6, h / 2.2, w / (n * 2));
  const step = n > 1 ? (w - dotR * 2) / (n - 1) : 0;
  const cy = y + h / 2;
  for (let i = 0; i < n; i++) drawGarmentDot(x + dotR + i * step, cy, dotR, color);
}

function drawBatchPile(x, y, w, h, count, seed = 1) {
  const n = Math.min(Math.max(0, Math.round(count)), 40);
  for (let i = 0; i < n; i++) {
    const a = ((seed + i * 7919) % 1000) / 1000;
    const b = ((seed + i * 9973) % 1000) / 1000;
    drawGarmentDot(x + 6 + a * (w - 12), y + 6 + b * (h - 12), 4.5, "#a29bfe");
  }
}

function fillGradient(x, y, w, h, pct) {
  const p = Math.min(1, Math.max(0, pct));
  ctx.fillStyle = "#3d4450";
  ctx.fillRect(x, y, w, h);
  const g = ctx.createLinearGradient(x, y, x, y + h);
  g.addColorStop(0, "#5a6268");
  g.addColorStop(0.5, p > 0.5 ? "#f39c12" : "#27ae60");
  g.addColorStop(1, p > 0.85 ? "#e74c3c" : "#2ecc71");
  ctx.fillStyle = g;
  ctx.fillRect(x, y + h * (1 - p), w, h * p);
}

function drawSpinner(cx, cy, r, progress) {
  ctx.strokeStyle = "#ffd93d";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * progress);
  ctx.stroke();
}

function drawArrow(x1, y1, x2, y2, color, width = 3) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const head = 10;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - head * Math.cos(angle - 0.35), y2 - head * Math.sin(angle - 0.35));
  ctx.lineTo(x2 - head * Math.cos(angle + 0.35), y2 - head * Math.sin(angle + 0.35));
  ctx.closePath();
  ctx.fill();
}

function uniformPoolQueue(poolId) {
  const peers = [...blockMap.values()].filter((b) => b.fifo_pool === poolId);
  if (!peers.length) return 0;
  const total = peers.reduce((s, p) => s + (unitQueue(p.id) || 0), 0);
  return Math.ceil(total / peers.length);
}

function groupPort(g, toward) {
  const bx = g._body_px ?? g._px;
  const bw = g._body_pw ?? g._pw;
  const cx = bx + bw / 2;
  if (toward === "down") return { x: cx, y: g._py + g._ph };
  if (toward === "up") return { x: cx, y: g._py };
  if (toward === "left") return { x: bx, y: g._py + g._ph / 2 };
  return { x: bx + bw, y: g._py + g._ph / 2 };
}

function drawBezierLink(p1, p2, color, width, dashed) {
  const dy = p2.y - p1.y;
  const c1y = p1.y + dy * 0.45;
  const c2y = p2.y - dy * 0.45;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = width;
  if (dashed) ctx.setLineDash([8, 6]);
  ctx.beginPath();
  ctx.moveTo(p1.x, p1.y);
  ctx.bezierCurveTo(p1.x, c1y, p2.x, c2y, p2.x, p2.y);
  ctx.stroke();
  ctx.setLineDash([]);
  const angle = Math.atan2(p2.y - c2y, p2.x - p1.x);
  const head = 9;
  ctx.beginPath();
  ctx.moveTo(p2.x, p2.y);
  ctx.lineTo(p2.x - head * Math.cos(angle - 0.4), p2.y - head * Math.sin(angle - 0.4));
  ctx.lineTo(p2.x - head * Math.cos(angle + 0.4), p2.y - head * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fill();
}

function drawGroupLinks() {
  for (const link of state.group_links || []) {
    const a = groupMap.get(link.from);
    const b = groupMap.get(link.to);
    if (!a || !b) continue;
    const rework = link.link_kind === "rework";
    const sameBand = Math.abs(a._py - b._py) < 80;
    let p1;
    let p2;
    if (rework || (sameBand && a._px < b._px)) {
      p1 = groupPort(a, "right");
      p2 = groupPort(b, "left");
    } else if (a._py + a._ph <= b._py + 20) {
      p1 = groupPort(a, "down");
      p2 = groupPort(b, "up");
    } else {
      p1 = groupPort(a, "up");
      p2 = groupPort(b, "down");
    }
    const col = rework ? "#e17055" : SCHEMA_COLORS[link.schema] || SCHEMA_COLORS.default;
    ctx.globalAlpha = rework ? 0.85 : 0.55;
    drawBezierLink(p1, p2, col, rework ? 2.5 : 3.5, rework);
    ctx.globalAlpha = 1;
    if (link.note && link.note.length < 24) {
      const mx = (p1.x + p2.x) / 2;
      const my = (p1.y + p2.y) / 2;
      setFont(F.xs);
      ctx.fillStyle = rework ? "#e17055" : "#9aa0a6";
      ctx.fillText(link.note, mx - 20, my - 4);
    }
  }
}

function drawIntraGroupPipeline(g) {
  const col = SCHEMA_COLORS[g.schema] || "#6a7380";
  const arrowPad = 12;
  for (const b of g.blocks || []) {
    if (!b.flow_next) continue;
    const next = blockMap.get(b.flow_next);
    if (!next) continue;
    const p1 = { x: b._px + b._pw / 2, y: b._py + b._ph + 4 };
    const p2 = { x: next._px + next._pw / 2, y: next._py - 4 };
    const midY = (p1.y + p2.y) / 2;
    ctx.globalAlpha = 0.5;
    ctx.strokeStyle = col;
    ctx.fillStyle = col;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p1.x, midY);
    ctx.lineTo(p2.x, midY);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
    const head = 8;
    ctx.beginPath();
    ctx.moveTo(p2.x, p2.y);
    ctx.lineTo(p2.x - head * 0.5, p2.y - head);
    ctx.lineTo(p2.x + head * 0.5, p2.y - head);
    ctx.closePath();
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

function drawGroupShell(g) {
  const dragging = pointer?.type === "group" && pointer.groupId === g.id;
  const bx = g._body_px ?? g._px;
  const bw = g._body_pw ?? g._pw;
  ctx.fillStyle = dragging ? "rgba(35, 42, 52, 0.98)" : "rgba(25, 30, 38, 0.94)";
  ctx.strokeStyle = SCHEMA_COLORS[g.schema] || "#5a6270";
  ctx.lineWidth = dragging ? 3 : 2;
  safeRoundRect(bx, g._py, bw, g._ph, g._backlog_pw ? 10 : 10);
  ctx.fill();
  ctx.stroke();
  const headerH = Math.min(48, Math.max(40, g._ph * 0.14));
  ctx.fillStyle = "rgba(40, 48, 58, 0.9)";
  ctx.fillRect(bx + 1, g._py + 1, bw - 2, headerH);
  setFont(F.lg, "bold");
  ctx.fillStyle = "#e8eaed";
  ctx.fillText(fitLabel(g.label, bw - 56, F.lg, "bold"), bx + 10, g._py + 22);
  setFont(F.xs, "bold");
  ctx.fillStyle = SCHEMA_COLORS[g.schema] || "#9aa0a6";
  ctx.fillText((g.schema || "").toUpperCase(), bx + bw - 52, g._py + 22);
  g._headerH = headerH;
}

function drawBlock(b) {
  const sel = b.id === selectedBlockId;
  ctx.strokeStyle = sel ? "#ffd93d" : "rgba(90,100,112,0.8)";
  ctx.lineWidth = sel ? 2.5 : 1;

  if (b.kind === "washer") {
    const ws = washerState(b.id);
    const cap = b.capacity_items || ws.bin_capacity || 100;
    const binH = b._ph * 0.44;
    const drumH = b._ph * 0.46;
    ctx.fillStyle = "#1e2328";
    ctx.beginPath();
    ctx.roundRect(b._px, b._py, b._pw, b._ph, 5);
    ctx.fill();
    ctx.stroke();
    setFont(F.sm, "bold");
    ctx.fillStyle = "#ccc";
    ctx.fillText(fitLabel(b.label, b._pw - 8, F.sm, "bold"), b._px + 4, b._py + 12);
    ctx.fillStyle = "rgba(30,40,55,0.95)";
    ctx.strokeStyle = "#4a9eff";
    ctx.beginPath();
    ctx.roundRect(b._px + 3, b._py + 14, b._pw - 6, binH, 3);
    ctx.fill();
    ctx.stroke();
    setFont(F.xs);
    ctx.fillStyle = "#8ab4f8";
    ctx.fillText("BIN", b._px + 6, b._py + 24);
    const bi = { x: b._px + 5, y: b._py + 26, w: b._pw - 10, h: binH - 14 };
    const inBin = ws.bin_fill || 0;
    const binFrac = Math.min(1, inBin / cap);
    if (binFrac > 0) {
      const filledH = Math.max(4, bi.h * binFrac);
      fillGradient(bi.x, bi.y + bi.h - filledH, bi.w, filledH, 1);
      drawLinedQueue(bi.x, bi.y + bi.h - filledH, bi.w, filledH, inBin, 14, "#7bed9f");
    } else {
      ctx.fillStyle = "#2a3038";
      ctx.fillRect(bi.x, bi.y, bi.w, bi.h);
    }
    setFont(F.xs);
    ctx.fillStyle = "#8fd694";
    ctx.fillText(`${inBin}/${cap}`, b._px + 4, b._py + binH + 8);
    const drumY = b._py + 14 + binH + 6;
    ctx.fillStyle = "#2d3436";
    ctx.strokeStyle = ws.in_cycle ? "#ffd93d" : "#6a7380";
    ctx.beginPath();
    ctx.roundRect(b._px + 3, drumY, b._pw - 6, drumH, 4);
    ctx.fill();
    ctx.stroke();
    setFont(F.xs, "bold");
    ctx.fillStyle = "#aaa";
    ctx.fillText("DRUM", b._px + 6, drumY + 10);
    const di = { x: b._px + 5, y: drumY + 12, w: b._pw - 10, h: drumH - 14 };
    if (ws.in_cycle && ws.batch_size > 0) {
      fillGradient(di.x, di.y, di.w, di.h, Math.min(1, ws.batch_size / cap));
      drawSpinner(di.x + di.w / 2, di.y + di.h / 2, Math.min(di.w, di.h) * 0.28, ws.cycle_progress || 0);
      setFont(F.xs);
      ctx.fillStyle = "#eee";
      ctx.fillText(`${ws.batch_size} in cycle`, b._px + 4, drumY + drumH - 4);
    } else {
      ctx.fillStyle = "#3d4450";
      ctx.fillRect(di.x, di.y, di.w, di.h);
    }
    return;
  }

  if (b.kind === "worker") {
    let q = unitQueue(b.id) || 0;
    if (b.stage === "scan_in") q = scanWorkerQueue(b.id);
    else if (b.uniform_fifo && b.fifo_pool) {
      q = uniformPoolQueue(b.fifo_pool);
    }
    const busy = activeSample()?.units?.[b.id]?.busy;
    const waitH = b._ph * 0.3;
    ctx.fillStyle = "#2a3038";
    ctx.beginPath();
    ctx.roundRect(b._px, b._py, b._pw, b._ph, 4);
    ctx.fill();
    ctx.stroke();
    setFont(F.sm, "bold");
    ctx.fillStyle = "#ddd";
    ctx.fillText(fitLabel(b.label, b._pw - 8, F.sm, "bold"), b._px + 4, b._py + 12);
    const wy = b._py + b._ph - waitH - 4;
    ctx.fillStyle = "#3d4450";
    ctx.fillRect(b._px + 3, wy, b._pw - 6, waitH);
    drawLinedQueue(b._px + 5, wy + 2, b._pw - 10, waitH - 4, q, 8, "#ff9f43");
    ctx.fillStyle = busy ? "#b8860b" : "#3d6b45";
    ctx.beginPath();
    ctx.roundRect(b._px + 3, b._py + 14, b._pw - 6, b._ph - waitH - 18, 3);
    ctx.fill();
    setFont(F.xs);
    ctx.fillStyle = "#eee";
    ctx.fillText(busy ? "working" : "idle", b._px + 5, b._py + 26);
    return;
  }

  if (b.kind === "buffer_fifo") {
    let count = 0;
    if (b.id === "pre_scan_waiting") count = zoneCount("pre_scan_waiting");
    else if (b.id === "post_scan_waiting") count = zoneCount("post_scan_waiting");
    ctx.fillStyle = "#252a32";
    ctx.beginPath();
    ctx.roundRect(b._px, b._py, b._pw, b._ph, 4);
    ctx.fill();
    ctx.stroke();
    setFont(F.sm, "bold");
    ctx.fillStyle = "#ccc";
    ctx.fillText(fitLabel(b.label, b._pw - 8, F.sm, "bold"), b._px + 4, b._py + 14);
    drawLinedQueue(b._px + 4, b._py + 20, b._pw - 8, b._ph - 26, count, 20, "#7bed9f");
    setFont(F.count, "bold");
    ctx.fillStyle = count > 50 ? "#ff6b6b" : "#8fd694";
    ctx.fillText(String(Math.round(count)), b._px + 4, b._py + b._ph - 6);
    return;
  }

  if (b.kind === "buffer_batch") {
    const count = zoneCount("separation_backlog");
    ctx.fillStyle = "#252a32";
    ctx.beginPath();
    ctx.roundRect(b._px, b._py, b._pw, b._ph, 4);
    ctx.fill();
    ctx.stroke();
    setFont(F.sm, "bold");
    ctx.fillText(fitLabel(b.label, b._pw - 8, F.sm, "bold"), b._px + 4, b._py + 14);
    setFont(F.count, "bold");
    ctx.fillStyle = count > 80 ? "#ff6b6b" : "#a29bfe";
    ctx.fillText(String(Math.round(count)), b._px + 4, b._py + b._ph - 6);
    drawBatchPile(b._px + 4, b._py + 6, b._pw - 8, b._ph - 10, count, b.id.length);
    return;
  }

  if (b.kind === "route_label") {
    setFont(F.xs, "bold");
    ctx.fillStyle = "#9aa0a6";
    ctx.fillText(fitLabel(b.label, b._pw - 4, F.xs, "bold"), b._px + 2, b._py + 10);
    return;
  }

  ctx.fillStyle = b.id === "scan_bypass" ? "#3a3540" : "#2d4a35";
  ctx.beginPath();
  ctx.roundRect(b._px, b._py, b._pw, b._ph, 4);
  ctx.fill();
  ctx.stroke();
  setFont(F.sm, "bold");
  ctx.fillStyle = "#eee";
  ctx.fillText(fitLabel(b.label, b._pw - 8, F.sm, "bold"), b._px + 4, b._py + 14);
  if (b.daily_items != null) {
    setFont(F.xs);
    ctx.fillStyle = "#aaa";
    ctx.fillText(`~${Math.round(b.daily_items)}/d`, b._px + 4, b._py + 28);
  }
}

let lastDrawError = "";

function draw() {
  syncClockDisplays();
  const wrap = document.getElementById("canvas-wrap");
  const cw = Math.max(wrap.clientWidth, 320);
  const ch = Math.max(wrap.clientHeight, 400);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#14171c";
  ctx.fillRect(0, 0, cw, ch);
  if (!(state.groups || []).length) {
    setFont(F.md);
    ctx.fillStyle = "#9aa0a6";
    ctx.fillText("Run simulation to load the plant map.", 24, 48);
    return;
  }
  try {
    ctx.save();
    const scale = Number.isFinite(view.scale) && view.scale > 0 ? view.scale : 1;
    ctx.translate(view.x, view.y);
    ctx.scale(scale, scale);
    drawGroupLinks();
    for (const g of state.groups || []) {
      drawBacklogPanel(g);
      drawGroupShell(g);
      drawIntraGroupPipeline(g);
      for (const b of g.blocks || []) drawBlock(b);
    }
    for (const p of particles) {
      p.t += p.speed * 0.016;
      if (p.t >= 1) continue;
      const t = Math.max(0, p.t);
      const cx = (1 - t) ** 2 * p.x + 2 * (1 - t) * t * ((p.x + p.tx) / 2) + t ** 2 * p.tx;
      const cy = (1 - t) ** 2 * p.y + 2 * (1 - t) * t * ((p.y + p.ty) / 2 - 12) + t ** 2 * p.ty;
      ctx.beginPath();
      ctx.arc(cx, cy, p.radius || 6, 0, Math.PI * 2);
      ctx.fillStyle = p.color;
      ctx.fill();
    }
    particles = particles.filter((p) => p.t < 1);
    ctx.restore();
    if (lastDrawError) {
      lastDrawError = "";
      errorEl.textContent = "";
    }
  } catch (err) {
    lastDrawError = err.message || String(err);
    errorEl.textContent = `Draw error: ${lastDrawError}`;
    console.error(err);
  }
}

function spawnMove(e) {
  const from = resolveBlock(e.fr);
  const to = resolveBlock(e.to);
  if (!from || !to) {
    if (!from && !to) {
      const gf = blockToGroup.get(e.fr) || blockToGroup.get(e.fr?.split(":")[0]);
      const gt = blockToGroup.get(e.to) || blockToGroup.get(e.to?.split(":")[0]);
      if (gf && gt && gf !== gt) {
        const a = groupMap.get(gf);
        const b = groupMap.get(gt);
        if (a && b) {
          particles.push({
            x: a._px + a._pw,
            y: a._py + a._ph / 2,
            tx: b._px,
            ty: b._py + b._ph / 2,
            t: 0,
            speed: 0.35,
            color: SCHEMA_COLORS[b.schema] || "#70a1ff",
          });
        }
      }
    }
    return;
  }
  const fc = blockCenter(from);
  const tc = blockCenter(to);
  const n = Math.min(e.n || 1, 6);
  const color =
    e.to.includes("press") || e.fr.includes("spotting")
      ? "#ffa502"
      : e.to === "press_conveyor" || e.fr === "press_conveyor"
        ? "#ffb142"
        : "#7bed9f";
  for (let i = 0; i < n; i++) {
    if (particles.length >= MAX_PARTICLES) return;
    particles.push({
      x: fc.x,
      y: fc.y,
      tx: tc.x,
      ty: tc.y,
      t: -i * 0.06,
      speed: 0.28 + Math.random() * 0.18,
      color,
      radius: 6,
    });
  }
}

function spawnParticlesForInterval(t0, t1) {
  const events = state.flow_events || [];
  while (eventCursor < events.length && events[eventCursor].t <= t1) {
    const e = events[eventCursor++];
    if (e.t >= t0 && e.kind === "move") spawnMove(e);
  }
}

function spawnParticlesForFrame() {
  if (playing) return;
  const s = samples();
  if (!s.length) return;
  const sample = s[currentSampleIndex()];
  if (!sample) return;
  const w0 = sample.t - (state.time_series?.interval_minutes || 1);
  let n = 0;
  for (let i = 0; i < state.flow_events.length && n < 12; i++) {
    const e = state.flow_events[i];
    if (e.kind !== "move" || e.t < w0 || e.t > sample.t) continue;
    if (Math.random() > 0.15) continue;
    spawnMove(e);
    n++;
  }
}

function loop(now) {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  if (playing) {
    readPlaybackSettings();
    const s = samples();
    if (!s.length) {
      playing = false;
    } else {
      playbackStepDebt += dt * snapshotsPerSecond();
      while (playbackStepDebt >= 1 && playing) {
        playbackStepDebt -= 1;
        const prevIdx = playbackSampleIndex;
        const prevT = s[prevIdx]?.t ?? 0;
        const maxI = validPlaybackMaxIndex();
        if (playbackSampleIndex >= maxI) {
          playbackStepDebt = 0;
          if (streamRecording && streamEventSource && maxI < s.length - 1) {
            statusEl.textContent = `Buffering… step ${maxI + 1}`;
          }
        } else {
          playbackSampleIndex = Math.min(playbackSampleIndex + 1, maxI);
          timelineIndex = playbackSampleIndex;
          timelineEl.value = String(playbackSampleIndex);
          const nextT = s[playbackSampleIndex]?.t ?? prevT;
          spawnParticlesForInterval(prevT, nextT);
          syncEventCursorToTime(nextT);
        }
        if (playbackSampleIndex >= maxI && maxI >= s.length - 1) {
          playing = false;
          document.getElementById("btn-play").textContent = "Play";
        }
      }
      updateTimeLabel();
    }
  }
  spawnParticlesForFrame();
  draw();
  requestAnimationFrame(loop);
}

function blockStatsFor(bid, b) {
  const bs = state.block_statistics || {};
  if (bs[bid]) return bs[bid];
  if (b?.stage && bs[b.stage]) return bs[b.stage];
  if (b?.fifo_pool && bs[b.fifo_pool]) return bs[b.fifo_pool];
  return null;
}

function initSnapshotIntervalControl() {
  const mount = document.getElementById("snapshot-interval-wrap");
  if (!mount || !window.DurationFields) return;
  let stored = 1;
  try {
    const saved = localStorage.getItem("viz_sample_interval_min");
    if (saved) stored = parseFloat(saved) || 1;
  } catch {
    /* ignore */
  }
  mount.innerHTML = window.DurationFields.rowHtml(
    "Snapshot interval",
    null,
    stored,
    "minutes",
    { simControl: "snapshot_interval" }
  );
  const field = mount.querySelector(".duration-field");
  if (!field) return;
  field.dataset.storedMinutes = String(stored);
  window.DurationFields.bind(field, {
    onChange: (w) => {
      const m = window.DurationFields.readWrapper(w);
      w.dataset.storedMinutes = String(m);
      try {
        localStorage.setItem("viz_sample_interval_min", String(m));
      } catch {
        /* ignore */
      }
    },
  });
}

function getSnapshotIntervalMinutes() {
  const wrap = document.querySelector('[data-sim-control="snapshot_interval"]');
  if (wrap && window.DurationFields) {
    return Math.max(0.1, window.DurationFields.readWrapper(wrap));
  }
  return 1;
}

function formatDurationSeconds(sec) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Number(sec);
  if (s >= 90) return `${s.toFixed(1)}s (${(s / 60).toFixed(2)} min)`;
  return `${s.toFixed(1)}s`;
}

function formatWorkedHours(d) {
  if (d.time_worked_hours != null) return `${d.time_worked_hours}h`;
  if (d.service_minutes != null) return `${(d.service_minutes / 60).toFixed(2)}h`;
  return "—";
}

function renderDailyTable(daily) {
  if (!daily?.length) {
    return "<p class=\"hint\">No per-day breakdown (re-run sim with operating days &gt; 1).</p>";
  }
  const rows = daily
    .map(
      (d) =>
        `<tr><td>Day ${d.operating_day}</td><td>${d.items_processed}</td>` +
        `<td>${d.avg_service_seconds ?? "—"}s</td>` +
        `<td>${formatWorkedHours(d)}</td>` +
        `<td>${d.wait_minutes != null ? Math.round(d.wait_minutes) + "m wait" : "—"}</td></tr>`
    )
    .join("");
  return `<table class="block-stats-table"><thead><tr><th>Day</th><th>Items</th><th>Avg proc</th><th>Worked</th><th>Wait</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderBlockStatsPanel(b, st) {
  if (!st) {
    return `<div>Group: <strong>${b._groupId}</strong></div>
      <div>~Daily (avg): <strong>${b.daily_items ?? "—"}</strong></div>
      <p class="hint">No detailed stats for this block.</p>`;
  }
  const utilPct = st.utilization != null ? (st.utilization * 100).toFixed(1) + "%" : "—";
  const shiftCap =
    st.shift_capacity_hours != null
      ? `${st.shift_capacity_hours}h shift capacity (${simDaysLabel()} days)`
      : "";
  return `
    <div class="block-stats-summary">
      <div>Items processed: <strong>${st.items_processed ?? 0}</strong></div>
      <div>Avg process time: <strong>${formatDurationSeconds(st.avg_service_seconds)}</strong> / item</div>
      <div>Avg wait (queue): <strong>${formatDurationSeconds(st.avg_wait_seconds)}</strong> / item</div>
      <div>Time worked (service): <strong>${st.time_worked_hours ?? "—"}h</strong></div>
      <div>Utilization: <strong>${utilPct}</strong>${shiftCap ? ` · ${shiftCap}` : ""}</div>
      ${st.max_queue != null ? `<div>Peak queue: <strong>${st.max_queue}</strong></div>` : ""}
    </div>
    <div class="block-stats-daily"><strong>By operating day</strong>${renderDailyTable(st.daily)}</div>`;
}

function simDaysLabel() {
  return state.config_snapshot?.simulation_days ?? state.summary?.simulation_days ?? "?";
}

function showBlockDetail(bid) {
  selectedBlockId = bid;
  const b = blockMap.get(bid);
  if (!b) return;
  unitPanel.classList.remove("hidden");
  const st = blockStatsFor(bid, b);
  if (b.kind === "washer") {
    const ws = washerState(bid);
    const cap = b.capacity_items || ws.bin_capacity || 100;
    unitDetailEl.innerHTML = `<div><strong>${b.label}</strong></div>
      <div>Bin: <strong>${ws.bin_fill ?? 0}</strong> / ${cap} (live snapshot)</div>
      <div>Drum: <strong>${ws.in_cycle ? ws.batch_size + " items" : "empty"}</strong>${ws.in_cycle ? " · " + Math.round((ws.cycle_progress || 0) * 100) + "% cycle" : ""}</div>
      ${renderBlockStatsPanel(b, st)}`;
  } else {
    unitDetailEl.innerHTML = `<div><strong>${b.label}</strong></div>
      ${renderBlockStatsPanel(b, st)}`;
  }
}

function updateFlowEventsWarning() {
  const s = state.summary || {};
  const ts = state.time_series;
  const warnings = [];
  if (s.flow_events_truncated || (s.flow_events_dropped || 0) > 0) {
    warnings.push(
      `Flow recording capped (${s.flow_events_dropped || 0} dropped). Animated dots may stop early.`
    );
  }
  if (s.flow_events_export_truncated) {
    warnings.push("Move events subsampled for export; some playback steps may have no dots.");
  }
  const lastT = s.flow_events_last_t;
  const horizon =
    ts?.playback_horizon_minutes ?? s.playback_horizon_minutes ?? null;
  if (lastT != null && horizon != null && lastT < horizon - 60) {
    warnings.push(
      `Last move event is before end of timeline (${Math.round(horizon - lastT)} sim-min gap).`
    );
  }
  errorEl.textContent = warnings.join(" ");
}

function fillSidebar(data) {
  const s = data.summary || {};
  const c = data.config_snapshot || {};
  const ng = (data.groups || []).length;
  kpiEl.innerHTML = `
    <div>Injected: <strong>${Math.round(s.items_injected || 0)}</strong></div>
    <div>Pipeline done: <strong>${s.items_completed ?? "—"}</strong></div>
    <div>Groups: <strong>${ng}</strong> · Washers: <strong>${c.washer_count ?? "—"}</strong></div>
    <div>Scan in: <strong>${c.scan_in_enabled ? "on" : "bypassed"}</strong></div>
  `;
  outboundKpiEl.innerHTML = `
    <div>Waiting to ship: <strong id="kpi-completed-waiting">—</strong></div>
    <div>Shipped on trucks: <strong>${s.items_shipped ?? 0}</strong></div>
    <div>Trucks departed: <strong>${s.trucks_departed ?? 0}</strong>${(s.partial_trucks || 0) > 0 ? ` · partial: ${s.partial_trucks}` : ""}</div>
  `;
  const assumptions = c.model_assumptions || [];
  modelAssumptionsEl.innerHTML = assumptions.length
    ? assumptions.map((line) => `<li>${line}</li>`).join("")
    : "<li>—</li>";
  const bn = s.bottlenecks || [];
  bottlenecksEl.innerHTML = bn.length
    ? bn.slice(0, 6).map((b) => `${b.stage}: ${(b.utilization * 100).toFixed(0)}%`).join("<br>")
    : "—";
  updateFlowEventsWarning();
  if (c.simulation_days != null) document.getElementById("inp-sim-days").value = c.simulation_days;
  if (c.sample_interval_minutes != null) {
    const wrap = document.querySelector('[data-sim-control="snapshot_interval"]');
    if (wrap && window.DurationFields) {
      wrap.dataset.storedMinutes = String(c.sample_interval_minutes);
      const unit = window.DurationFields.defaultUnit("minutes", c.sample_interval_minutes);
      const unitSel = wrap.querySelector(".duration-unit");
      if (unitSel) unitSel.value = unit;
      const input = wrap.querySelector(".duration-value");
      if (input) {
        input.value = window.DurationFields.formatDisplayNum(
          window.DurationFields.toDisplay(c.sample_interval_minutes, "minutes", unit),
          "minutes",
          unit
        );
      }
    }
  }
  const eff = data.effective_config;
  if (eff && window.PlantConfigEditor?.applyEffectiveConfigAfterRun) {
    window.PlantConfigEditor.applyEffectiveConfigAfterRun(eff, c);
  } else if (c.plant_close && window.PlantConfigEditor?.updateRunHint) {
    window.PlantConfigEditor.updateRunHint(eff || {}, c);
  }
}

function setSimRunning(running) {
  btnRefresh.disabled = running;
  btnWeek.disabled = running;
  simProgressWrap.classList.toggle("hidden", !running);
}

function updateSimProgress(phase, current, total, message) {
  const t = Math.max(total, 1);
  const pct = Math.min(100, Math.round((100 * current) / t));
  simProgressEl.value = pct;
  const phaseLabel =
    phase === "simulate"
      ? `Simulating — ${current}/${total} operating days`
      : phase === "drain"
        ? "Draining WIP…"
        : phase === "build_graph"
          ? "Building graph…"
          : message || phase;
  simProgressLabel.textContent = message || phaseLabel;
  statusEl.textContent = phaseLabel;
}

async function pollSimJob(jobId) {
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  for (;;) {
    const res = await fetch(
      `/api/simulate/status?job_id=${encodeURIComponent(jobId)}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (data.status === "running") {
      updateSimProgress(
        data.phase || "simulate",
        data.current ?? 0,
        data.total ?? 1,
        data.message
      );
      await delay(200);
      continue;
    }
    if (data.status === "done") return data.result;
    if (data.status === "error") throw new Error(data.error || data.message || "Simulation failed");
    await delay(200);
  }
}

function applySimulationResult(data, memoOpts = {}) {
  state = data;
  particles = [];
  eventCursor = 0;
  playbackSampleIndex = 0;
  timelineIndex = 0;
  playbackStepDebt = 0;
  resize();
  for (const g of state.groups || []) {
    g._ux = 0;
    g._uy = 0;
  }
  layoutAll(false);
  resize();
  fitViewToLayout();
  fillSidebar(data);
  setupTimeline();
  draw();
  if (memoOpts.fromCache) return;
  const cfg = memoOpts.config || getSimConfig();
  if (!cfg) return;
  const forkIndex = memoOpts.forkIndex ?? -1;
  saveSimMemo(
    computeSimMemoKey(
      cfg,
      memoOpts.seed ?? 42,
      getSnapshotIntervalMinutes(),
      forkIndex
    ),
    data,
    { forkIndex }
  );
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(",")}}`;
}

function hashString(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) + h) ^ text.charCodeAt(i);
  }
  return (h >>> 0).toString(16);
}

function computeSimMemoKey(config, seed, intervalMinutes, forkIndex) {
  return hashString(
    stableStringify({ config, seed, intervalMinutes, forkIndex: forkIndex ?? -1 })
  );
}

function loadSimMemoIndex() {
  try {
    return JSON.parse(localStorage.getItem(SIM_MEMO_INDEX_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveSimMemoIndex(index) {
  localStorage.setItem(SIM_MEMO_INDEX_KEY, JSON.stringify(index.slice(0, MAX_SIM_MEMO_ENTRIES)));
}

function loadSimMemo(key) {
  try {
    const raw = localStorage.getItem(`${SIM_MEMO_PREFIX}${key}`);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveSimMemo(key, graph, meta = {}) {
  try {
    const payload = {
      savedAt: new Date().toISOString(),
      sampleCount: graph?.time_series?.samples?.length ?? 0,
      forkIndex: meta.forkIndex ?? -1,
      graph,
    };
    localStorage.setItem(`${SIM_MEMO_PREFIX}${key}`, JSON.stringify(payload));
    let index = loadSimMemoIndex().filter((e) => e.key !== key);
    index.unshift({
      key,
      savedAt: payload.savedAt,
      sampleCount: payload.sampleCount,
      forkIndex: payload.forkIndex,
    });
    const dropped = index.slice(MAX_SIM_MEMO_ENTRIES);
    index = index.slice(0, MAX_SIM_MEMO_ENTRIES);
    saveSimMemoIndex(index);
    for (const e of dropped) {
      localStorage.removeItem(`${SIM_MEMO_PREFIX}${e.key}`);
    }
  } catch {
    /* quota or private mode */
  }
}

function buildExportPayload() {
  const intervalMin = getSnapshotIntervalMinutes();
  const seed = 42;
  const forkIndex = streamRecording?.forkIndex ?? -1;
  const memoKey = computeSimMemoKey(getSimConfig(), seed, intervalMin, forkIndex);
  const baseMeta = {
    seed,
    memoKey,
    streaming: !!streamEventSource,
    branchFidelityNote: BRANCH_FIDELITY_NOTE,
  };
  if (streamRecording?.layout?.groups) {
    return streamRecording.toExportGraph(state, intervalMin, baseMeta);
  }
  return {
    format: "plant-viz-recording",
    version: 1,
    exportedAt: new Date().toISOString(),
    partial: false,
    branch_fidelity_note: BRANCH_FIDELITY_NOTE,
    groups: state.groups,
    group_links: state.group_links,
    layout_meta: state.layout_meta,
    edges: state.edges,
    time_series: state.time_series,
    flow_events: state.flow_events,
    summary: state.summary,
    block_statistics: state.block_statistics,
    effective_config: state.effective_config,
    config_snapshot: state.config_snapshot,
    seed,
    sample_interval_minutes: intervalMin,
    memo_key: memoKey,
  };
}

function exportRecording() {
  const payload = buildExportPayload();
  if (!payload.groups?.length && !payload.time_series?.samples?.length) return;
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  const days = payload.config_snapshot?.simulation_days ?? "run";
  const tag = payload.partial ? "partial" : "complete";
  a.download = `plant-recording-${days}d-${tag}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function applyPartialFromRecording(recording) {
  if (!recording?.layout?.groups) return;
  state.groups = recording.layout.groups;
  state.group_links = recording.layout.group_links || [];
  state.layout_meta = recording.layout.layout_meta || {};
  state.time_series = recording.toTimeSeries(getSnapshotIntervalMinutes());
  state.flow_events = recording.flowEvents;
  for (const g of state.groups || []) {
    g._ux = g._ux || 0;
    g._uy = g._uy || 0;
  }
  layoutAll(false);
  setupTimeline();
  draw();
}

function runStreamSimulation(simConfig) {
  if (!window.SimRecording) {
    return pollSimJobOnly(simConfig);
  }
  const recording = new window.SimRecording();
  streamRecording = recording;
  recording.reset();

  return fetch("/api/sim/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      seed: 42,
      sample_interval_minutes: getSnapshotIntervalMinutes(),
      batch_size: 20,
      config: simConfig,
    }),
  }).then((res) =>
    res.json().then((started) => {
      if (!res.ok) throw new Error(started.error || res.statusText);
      if (!started.job_id) throw new Error("Server did not return job_id");
      currentStreamJobId = started.job_id;
      return new Promise((resolve, reject) => {
        if (streamEventSource) streamEventSource.close();
        const es = new EventSource(
          `/api/sim/stream?job_id=${encodeURIComponent(started.job_id)}`
        );
        streamEventSource = es;
        es.addEventListener("sim_init", (ev) => {
          recording.applyInit(JSON.parse(ev.data));
          applyPartialFromRecording(recording);
        });
        es.addEventListener("sim_fork", (ev) => {
          recording.onFork(JSON.parse(ev.data));
          timelineIndex = Math.min(timelineIndex, Math.max(0, recording.forkIndex));
          playbackSampleIndex = timelineIndex;
          timelineEl.value = String(timelineIndex);
          applyPartialFromRecording(recording);
          statusEl.textContent = `Recalculating from step ${recording.forkIndex}…`;
        });
        es.addEventListener("sim_batch", (ev) => {
          recording.applyBatch(JSON.parse(ev.data));
          applyPartialFromRecording(recording);
          const n = recording.validMaxIndex() + 1;
          statusEl.textContent = `Streaming… ${n} snapshot(s)`;
        });
        es.addEventListener("sim_done", () => {
          es.close();
          streamEventSource = null;
          pollSimJob(started.job_id).then(resolve).catch(reject);
        });
        es.addEventListener("sim_error", (ev) => {
          es.close();
          streamEventSource = null;
          let msg = "stream error";
          try {
            msg = JSON.parse(ev.data).error || msg;
          } catch {
            /* ignore */
          }
          reject(new Error(msg));
        });
      });
    })
  );
}

async function requestConfigBranch() {
  if (!currentStreamJobId || !streamRecording) return;
  const forkIndex = timelineIndex;
  if (forkIndex < 0) return;
  const simConfig = getSimConfig();
  if (!simConfig) return;
  streamRecording.truncateAfter(forkIndex);
  statusEl.textContent = `Branching from step ${forkIndex}…`;
  try {
    const res = await fetch("/api/sim/branch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: currentStreamJobId,
        fork_index: forkIndex,
        config: simConfig,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
  } catch (e) {
    errorEl.textContent = e.message;
  }
}

function scheduleConfigBranch() {
  if (!currentStreamJobId) return;
  clearTimeout(configBranchTimer);
  configBranchTimer = setTimeout(() => requestConfigBranch(), 500);
}

window.addEventListener("plant-config-changed", () => scheduleConfigBranch());

async function pollSimJobOnly(simConfig) {
  const res = await fetch("/api/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      seed: 42,
      sample_interval_minutes: getSnapshotIntervalMinutes(),
      config: simConfig,
    }),
  });
  const started = await res.json();
  if (!res.ok) throw new Error(started.error || res.statusText);
  return pollSimJob(started.job_id);
}

async function refresh() {
  statusEl.textContent = "Simulating…";
  errorEl.textContent = "";
  playing = false;
  document.getElementById("btn-play").textContent = "Play";
  setSimRunning(true);
  updateSimProgress("simulate", 0, 1, "Starting…");
  try {
    readPlaybackSettings();
    const simConfig = getSimConfig();
    if (!simConfig) throw new Error("Plant config not loaded");
    const intervalMin = getSnapshotIntervalMinutes();
    const seed = 42;
    const memoKey = computeSimMemoKey(simConfig, seed, intervalMin, -1);
    const cached = loadSimMemo(memoKey);
    if (cached?.graph?.groups?.length) {
      applySimulationResult(cached.graph, {
        config: simConfig,
        seed,
        forkIndex: -1,
        fromCache: true,
      });
      window.PlantConfigPresets?.renderPresetList?.();
      statusEl.textContent = "Ready (cached)";
      return;
    }
    const days = simConfig.objectives?.simulation_days ?? 1;
    const close = simConfig.calendar?.wash_cutoff_time ?? "—";
    const trSep = simConfig.transfers?.after_separation ?? "—";
    updateSimProgress(
      "simulate",
      0,
      days,
      `Running ${days} day(s), close ${close}, after_separation ${trSep} min…`
    );
    const data = await runStreamSimulation(simConfig);
    const forkIndex = streamRecording?.forkIndex ?? -1;
    streamRecording = null;
    applySimulationResult(data, { config: simConfig, seed: 42, forkIndex });
    window.PlantConfigPresets?.renderPresetList?.();
    statusEl.textContent = "Ready";
  } catch (e) {
    errorEl.textContent = e.message;
    statusEl.textContent = "Error";
  } finally {
    setSimRunning(false);
  }
}

function getSimConfig() {
  if (window.PlantConfigEditor) {
    window.PlantConfigEditor.flushToLiveConfig?.();
    const cfg = window.PlantConfigEditor.getLiveConfig();
    if (cfg) return cfg;
  }
  return null;
}

document.getElementById("btn-refresh").addEventListener("click", refresh);
document.getElementById("btn-week").addEventListener("click", () => {
  document.getElementById("inp-sim-days").value = "7";
  if (window.PlantConfigEditor) {
    window.PlantConfigEditor.syncSimControlsToConfig();
    const cfg = window.PlantConfigEditor.getLiveConfig();
    if (cfg?.objectives) cfg.objectives.simulation_days = 7;
  }
  refresh();
});
document.getElementById("btn-play").addEventListener("click", () => {
  if (!playing) {
    readPlaybackSettings();
    playbackStepDebt = 0;
    playbackSampleIndex = timelineIndex;
    const s = samples();
    syncEventCursorToTime(s[playbackSampleIndex]?.t ?? 0);
  }
  playing = !playing;
  document.getElementById("btn-play").textContent = playing ? "Pause" : "Play";
  if (playing) updateTimeLabel();
});
document.getElementById("btn-export-recording")?.addEventListener("click", exportRecording);
document.getElementById("btn-live").addEventListener("click", () => {
  const s = samples();
  playing = false;
  document.getElementById("btn-play").textContent = "Play";
  if (s.length) {
    const maxI = validPlaybackMaxIndex();
    playbackSampleIndex = maxI;
    timelineIndex = playbackSampleIndex;
    timelineEl.value = String(playbackSampleIndex);
    syncEventCursorToTime(s[playbackSampleIndex].t);
  }
  particles = [];
  updateTimeLabel();
});
timelineEl.addEventListener("input", () => {
  timelineIndex = parseInt(timelineEl.value, 10);
  playbackSampleIndex = timelineIndex;
  playing = false;
  document.getElementById("btn-play").textContent = "Play";
  const s = samples();
  syncEventCursorToTime(s[timelineIndex]?.t ?? 0);
  particles = [];
  updateTimeLabel();
});
function onPointerDown(ev) {
  if (ev.button !== 0) return;
  const { sx, sy } = canvasPointFromEvent(ev);
  const { x: wx, y: wy } = screenToWorld(sx, sy);
  const grp = groupAt(wx, wy);
  const wrap = document.getElementById("canvas-wrap");
  if (grp) {
    ev.preventDefault();
    pointer = {
      type: "group",
      groupId: grp.id,
      startX: sx,
      startY: sy,
      origUx: grp._ux || 0,
      origUy: grp._uy || 0,
    };
    wrap.classList.add("dragging-group");
    return;
  }
  pointer = { type: "pan", startX: sx, startY: sy, origX: view.x, origY: view.y };
  wrap.classList.add("panning");
}

function onPointerMove(ev) {
  if (!pointer) return;
  const { sx, sy } = canvasPointFromEvent(ev);
  if (pointer.type === "pan") {
    view.x = pointer.origX + (sx - pointer.startX);
    view.y = pointer.origY + (sy - pointer.startY);
    return;
  }
  if (pointer.type === "group") {
    const g = groupMap.get(pointer.groupId);
    if (!g) return;
    const dx = (sx - pointer.startX) / view.scale;
    const dy = (sy - pointer.startY) / view.scale;
    g._ux = pointer.origUx + dx;
    g._uy = pointer.origUy + dy;
    layoutAll(false);
  }
}

function onPointerUp(ev) {
  if (!pointer) return;
  const wrap = document.getElementById("canvas-wrap");
  wrap.classList.remove("panning", "dragging-group");
  if (pointer.type === "group") {
    const all = loadGroupOffsets();
    const g = groupMap.get(pointer.groupId);
    if (g) all[g.id] = { dx: g._ux, dy: g._uy };
    saveGroupOffsets(all);
  } else if (pointer.type === "pan" && ev) {
    const { sx, sy } = canvasPointFromEvent(ev);
    const moved = Math.hypot(sx - pointer.startX, sy - pointer.startY);
    if (moved < 6) {
      const { x: wx, y: wy } = screenToWorld(sx, sy);
      const b = blockAt(wx, wy);
      if (b) showBlockDetail(b.id);
    }
  }
  pointer = null;
}

canvas.addEventListener("mousedown", onPointerDown);
window.addEventListener("mousemove", onPointerMove);
window.addEventListener("mouseup", onPointerUp);
canvas.addEventListener(
  "wheel",
  (ev) => {
    ev.preventDefault();
    const { sx, sy } = canvasPointFromEvent(ev);
    const before = screenToWorld(sx, sy);
    const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
    view.scale = Math.min(3, Math.max(0.25, view.scale * factor));
    const after = screenToWorld(sx, sy);
    view.x += (before.x - after.x) * view.scale;
    view.y += (before.y - after.y) * view.scale;
  },
  { passive: false }
);

document.getElementById("btn-fit-view").addEventListener("click", () => {
  fitViewToLayout();
});
document.getElementById("btn-reset-layout").addEventListener("click", () => {
  localStorage.removeItem(LAYOUT_STORE_KEY);
  for (const g of state.groups || []) {
    g._ux = 0;
    g._uy = 0;
  }
  layoutAll(false);
  fitViewToLayout();
});

window.addEventListener("resize", () => {
  resize();
  if ((state.groups || []).length) fitViewToLayout();
});

async function bootstrap() {
  resize();
  initSnapshotIntervalControl();
  try {
    if (window.PlantConfigEditor) {
      await window.PlantConfigEditor.loadBaseline();
      const editor = document.getElementById("config-editor");
      if (editor) window.PlantConfigEditor.renderEditor(editor);
      window.PlantConfigEditor.updateJsonArea();
      const days = document.getElementById("inp-sim-days");
      const cfg = window.PlantConfigEditor.getLiveConfig();
      if (days && cfg?.objectives) days.value = cfg.objectives.simulation_days;
      window.PlantConfigPresets?.renderPresetList?.();
    }
  } catch (e) {
    errorEl.textContent = `Config load failed: ${e.message}`;
    statusEl.textContent = "Config error";
    requestAnimationFrame(loop);
    return;
  }
  refresh();
  requestAnimationFrame(loop);
}

bootstrap();
