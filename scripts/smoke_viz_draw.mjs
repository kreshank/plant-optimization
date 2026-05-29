/**
 * Smoke test: load simulate JSON and run layout + draw path (Node canvas mock).
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const jsonPath =
  process.argv[2] || path.join(root, "outputs", "_smoke_sim.json");

const data = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
if (!data.groups?.length) {
  console.error("no groups in payload");
  process.exit(1);
}

const errors = [];
const ctx = {
  font: "",
  fillStyle: "",
  strokeStyle: "",
  lineWidth: 1,
  textAlign: "left",
  textBaseline: "alphabetic",
  globalAlpha: 1,
  setTransform() {},
  save() {},
  restore() {},
  beginPath() {},
  moveTo() {},
  lineTo() {},
  bezierCurveTo() {},
  closePath() {},
  fill() {},
  stroke() {},
  arc() {},
  fillRect() {},
  setLineDash() {},
  createLinearGradient() {
    return { addColorStop() {} };
  },
  measureText(text) {
    return { width: String(text).length * 7 };
  },
  roundRect(x, y, w, h, radii) {
    if (Array.isArray(radii) && radii.some((r) => typeof r !== "number" || r < 0)) {
      throw new TypeError(`invalid radii: ${JSON.stringify(radii)}`);
    }
  },
};

const canvas = {
  width: 800,
  height: 600,
  getContext: () => ctx,
};

// Minimal globals used by app logic (extracted patterns)
const BACKLOG_PANEL_DEFAULT = 92;
const MIN_BLOCK_W = 88;
const MIN_BLOCK_H = 48;

function layoutGroups(groups) {
  for (const g of groups) {
    g._ux = 0;
    g._uy = 0;
    g._px = (g.x_px ?? 0) + g._ux;
    g._py = (g.y_px ?? 36) + g._uy;
    g._pw = Math.max(g.width_px ?? 150, 120);
    g._ph = Math.max(g.height_px ?? 200, 120);
    g._backlog_pw = g.group_backlog ? g.backlog_panel_px || BACKLOG_PANEL_DEFAULT : 0;
    g._body_px = g._px + g._backlog_pw;
    g._body_pw = Math.max(g._pw - g._backlog_pw, g.body_width_px || g._pw, MIN_BLOCK_W);
    for (const b of g.blocks || []) {
      b._px = g._body_px + b.rel_x * g._body_pw;
      b._py = g._py + b.rel_y * g._ph;
      b._pw = Math.max(b.rel_w * g._body_pw, MIN_BLOCK_W);
      b._ph = Math.max(b.rel_h * g._ph, MIN_BLOCK_H);
    }
  }
}

function drawBacklogPanel(g) {
  if (!g.group_backlog || !g._backlog_pw) return;
  const pw = g._backlog_pw;
  const ph = g._ph;
  ctx.roundRect(g._px, g._py, pw, ph, [8, 0, 0, 8]);
}

function drawGroupShell(g) {
  const bx = g._body_px ?? g._px;
  const bw = g._body_pw ?? g._pw;
  const r = g._backlog_pw ? [0, 10, 10, 0] : 10;
  ctx.roundRect(bx, g._py, bw, g._ph, r);
}

layoutGroups(data.groups);
for (const g of data.groups) {
  try {
    drawBacklogPanel(g);
    drawGroupShell(g);
  } catch (e) {
    errors.push(`${g.id}: ${e.message}`);
  }
}

if (errors.length) {
  console.error("draw errors:", errors);
  process.exit(1);
}
console.log(`ok: laid out ${data.groups.length} groups, drew shells`);
