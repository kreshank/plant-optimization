/**
 * In-memory plant config editor (baseline from GET /api/config).
 */
(function () {
  const STAGE_IDS = [
    "scan_in",
    "separation",
    "steam_tunnel",
    "jacket_press",
    "general_press",
    "final_qc",
    "spotting",
    "delivery_scan",
    "outbound_scan",
  ];

  const TRANSFER_KEYS = [
    "after_scan_in",
    "to_wash",
    "after_wash",
    "after_separation",
    "after_spotting",
    "after_steam_tunnel",
    "after_jacket_press",
    "after_general_press",
    "after_final_qc",
    "after_delivery_scan",
    "after_outbound_scan",
  ];

  let baselineConfig = null;
  let liveConfig = null;

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function jsonEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function stageWorkers(stage) {
    const w = stage?.workers;
    if (typeof w === "number") return w;
    if (w?.count != null) return w.count;
    if (w?.normal != null) return w.normal;
    return 1;
  }

  function setStageWorkers(stage, n) {
    if (!stage.workers || typeof stage.workers === "number") {
      stage.workers = { count: n };
    } else {
      stage.workers.count = n;
      stage.workers.normal = n;
    }
  }

  function isDirty(path) {
    if (!baselineConfig || !liveConfig) return false;
    const parts = path.split(".");
    let b = baselineConfig;
    let l = liveConfig;
    for (const p of parts) {
      b = b?.[p];
      l = l?.[p];
    }
    return !jsonEqual(b, l);
  }

  function markDirty(el, path) {
    if (!el) return;
    el.classList.toggle("config-dirty", isDirty(path));
  }

  function markDurationDirty(wrapper) {
    const path = wrapper.dataset.path;
    if (!path) return;
    const parts = path.split(".");
    let b = baselineConfig;
    let l = liveConfig;
    for (const p of parts) {
      b = b?.[p];
      l = l?.[p];
    }
    const dirty = Number(b) !== Number(l);
    wrapper.classList.toggle("config-dirty", dirty);
    const label = wrapper.closest(".config-duration-label");
    if (label) label.classList.toggle("config-dirty", dirty);
  }

  function applyDurationField(wrapper, opts = {}) {
    const path = wrapper.dataset.path;
    if (!path || !liveConfig || !window.DurationFields) return;
    const stored = window.DurationFields.readWrapper(wrapper);
    if (path.startsWith("calendar.breaks.0.")) {
      if (!liveConfig.calendar.breaks?.length) {
        liveConfig.calendar.breaks = [{ start: "11:30", end: "12:30" }];
      }
    }
    if (path.includes(".shift.") && path.startsWith("stages.")) {
      const sid = path.split(".")[1];
      const part = path.split(".").slice(3).join(".");
      if (!liveConfig.stages[sid]) liveConfig.stages[sid] = {};
      if (!liveConfig.stages[sid].shift) liveConfig.stages[sid].shift = {};
      setByPath(liveConfig.stages[sid].shift, part, stored);
    } else {
      setByPath(liveConfig, path, stored);
    }
    if (!opts.silent) {
      markDurationDirty(wrapper);
      updateJsonArea();
      window.dispatchEvent(new CustomEvent("plant-config-changed"));
    }
  }

  function durationRow(label, path, stored, storage) {
    return window.DurationFields.rowHtml(label, path, stored, storage);
  }

  function renderEditor(container) {
    if (!liveConfig) return;
    const cal = liveConfig.calendar || {};
    const br = (cal.breaks && cal.breaks[0]) || { start: "11:30", end: "12:30" };
    let html = `
      <fieldset><legend>Calendar</legend>
        <label>Day open <input type="time" data-path="calendar.day_open_time" data-type="time-of-day" value="${toTimeInput(cal.day_open_time)}" /></label>
        <label>Wash intake cutoff <input type="time" data-path="calendar.wash_intake_cutoff_time" data-type="time-of-day" value="${toTimeInput(cal.wash_intake_cutoff_time || "17:00")}" /></label>
        <label>Plant close <input type="time" data-path="calendar.wash_cutoff_time" data-type="time-of-day" value="${toTimeInput(cal.wash_cutoff_time)}" /></label>
        <label>Lunch start <input type="time" data-path="calendar.breaks.0.start" data-type="time-of-day" value="${toTimeInput(br.start)}" /></label>
        <label>Lunch end <input type="time" data-path="calendar.breaks.0.end" data-type="time-of-day" value="${toTimeInput(br.end)}" /></label>
      </fieldset>
      <fieldset><legend>Objectives &amp; items</legend>
        <label>Items / truck <input type="number" data-path="items_per_truck" step="0.01" value="${liveConfig.items_per_truck ?? ""}" /></label>
        <label>Operating days <input type="number" data-path="objectives.simulation_days" min="1" max="14" step="1" value="${liveConfig.objectives?.simulation_days ?? 1}" /></label>
        <label>Daily target <input type="number" data-path="objectives.daily_items_target" step="1" value="${liveConfig.objectives?.daily_items_target ?? ""}" /></label>
        <label>Delivery deadline <input type="time" data-path="objectives.delivery_ready_deadline" data-type="time-of-day" value="${toTimeInput(liveConfig.objectives?.delivery_ready_deadline || "06:00")}" /></label>
      </fieldset>
      <fieldset><legend>Routing (%)</legend>
        <label>Spotting <input type="number" data-path="routing.after_separation.pct_spotting" step="0.1" value="${liveConfig.routing?.after_separation?.pct_spotting ?? 0}" /></label>
        <label>Steam <input type="number" data-path="routing.after_separation.pct_steam_tunnel" step="0.1" value="${liveConfig.routing?.after_separation?.pct_steam_tunnel ?? 0}" /></label>
        <label>Jacket press <input type="number" data-path="routing.after_separation.pct_jacket_press" step="0.1" value="${liveConfig.routing?.after_separation?.pct_jacket_press ?? 0}" /></label>
        <label>General press <input type="number" data-path="routing.after_separation.pct_general_press" step="0.1" value="${liveConfig.routing?.after_separation?.pct_general_press ?? ""}" /></label>
        <label>Steam → press % <input type="number" data-path="routing.after_steam.pct_needs_press" step="0.1" value="${liveConfig.routing?.after_steam?.pct_needs_press ?? 0}" /></label>
      </fieldset>
      <fieldset><legend>Policies</legend>
        <label>Wash min fill (0–1) <input type="number" data-path="policies.wash_batching.min_fill_ratio" min="0" max="1" step="0.01" value="${liveConfig.policies?.wash_batching?.min_fill_ratio ?? 0.85}" /></label>
        <label>Outbound mode
          <select data-path="policies.outbound_delivery.mode">
            <option value="both">both</option>
            <option value="end_of_day_cohort">end_of_day_cohort</option>
            <option value="csv_outgoing">csv_outgoing</option>
          </select>
        </label>
        <label>QC max rework <input type="number" data-path="policies.qc_rework.max_cycles" min="1" step="1" value="${liveConfig.policies?.qc_rework?.max_cycles ?? 3}" /></label>
      </fieldset>
      <fieldset><legend>Scan</legend>
        ${durationRow("Scan overhead / item", "scan_seconds_per_item", liveConfig.scan_seconds_per_item ?? 0, "seconds")}
      </fieldset>
      <fieldset><legend>Transfers (between stations)</legend>
        <p class="hint">Stored as sim-minutes in YAML; edit in sec or min.</p>`;
    for (const key of TRANSFER_KEYS) {
      const v = liveConfig.transfers?.[key] ?? 0;
      html += durationRow(key, `transfers.${key}`, v, "minutes");
    }
    html += `</fieldset><fieldset><legend>Washers</legend>`;
    (liveConfig.resources?.washers || []).forEach((w, i) => {
      html += `
        <div class="config-washer">
          <strong>${w.id}</strong>
          <label>Count <input type="number" data-path="resources.washers.${i}.count" min="1" step="1" value="${w.count}" /></label>
          ${durationRow("Cycle time", `resources.washers.${i}.cycle_minutes`, w.cycle_minutes, "minutes")}
          <label>Capacity <input type="number" data-path="resources.washers.${i}.capacity_items" min="1" step="1" value="${w.capacity_items}" /></label>
        </div>`;
    });
    html += `</fieldset><fieldset><legend>Stages</legend>`;
    for (const sid of STAGE_IDS) {
      const st = liveConfig.stages?.[sid] || {};
      const shift = st.shift || {};
      html += `<details class="config-stage"><summary>${sid}</summary>`;
      if (st.enabled !== undefined) {
        html += `<label><input type="checkbox" data-path="stages.${sid}.enabled" data-type="bool" ${st.enabled ? "checked" : ""} /> enabled</label>`;
      }
      html += `
        <label>Workers <input type="number" data-path="stages.${sid}._workers" data-type="stage-workers" data-stage="${sid}" min="0" step="1" value="${stageWorkers(st)}" /></label>`;
      if (st.throughput_items_per_hour == null) {
        html += durationRow(
          "Service time",
          `stages.${sid}.service_time_seconds`,
          st.service_time_seconds ?? 0,
          "seconds"
        );
      }
      if (st.throughput_items_per_hour != null) {
        html += `<label>Throughput / hr <input type="number" data-path="stages.${sid}.throughput_items_per_hour" step="0.1" min="0" value="${st.throughput_items_per_hour}" /></label>`;
      }
      if (sid === "steam_tunnel" && (st.post_check_seconds != null || st.post_check_seconds === 0)) {
        html += durationRow(
          "Post-check time",
          `stages.${sid}.post_check_seconds`,
          st.post_check_seconds ?? 0,
          "seconds"
        );
      }
      html += `
        <label>Shift start <input type="time" data-path="stages.${sid}.shift.start" data-type="time-of-day" value="${toTimeInput(shift.start || "08:00")}" /></label>
        <label>Shift end <input type="time" data-path="stages.${sid}.shift.end" data-type="time-of-day" value="${toTimeInput(shift.end || "17:00")}" /></label>`;
      if (sid === "final_qc") {
        html += `<label>Defect rate <input type="number" data-path="stages.final_qc.defect_rate" min="0" max="1" step="0.01" value="${st.defect_rate ?? 0}" /></label>`;
      }
      html += `</details>`;
    }
    html += `</fieldset>`;
    container.innerHTML = html;

    const modeSel = container.querySelector('[data-path="policies.outbound_delivery.mode"]');
    if (modeSel) modeSel.value = liveConfig.policies?.outbound_delivery?.mode || "both";

    container.querySelectorAll("[data-path]").forEach((el) => {
      if (el.closest(".duration-field")) return;
      el.addEventListener("change", () => applyField(el));
      el.addEventListener("input", () => markDirty(el, el.dataset.path));
    });
    if (window.DurationFields) {
      window.DurationFields.bindAll(container, {
        onChange: applyDurationField,
        isDirty: markDurationDirty,
      });
    }
    refreshDirtyMarks(container);
    container.querySelectorAll(".duration-field[data-path]").forEach(markDurationDirty);
  }

  function toTimeInput(hhmm) {
    if (!hhmm) return "08:00";
    const p = String(hhmm).trim().split(":");
    return `${p[0].padStart(2, "0")}:${(p[1] || "0").padStart(2, "0")}`;
  }

  function fromTimeInput(val) {
    if (!val) return "08:00";
    const [h, m] = val.split(":");
    return `${parseInt(h, 10)}:${m}`;
  }

  function setByPath(obj, path, value) {
    const parts = path.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (cur[p] == null || typeof cur[p] !== "object") {
        cur[p] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      }
      cur = cur[p];
    }
    cur[parts[parts.length - 1]] = value;
  }

  function applyField(el, opts = {}) {
    const path = el.dataset.path;
    if (!path || !liveConfig) return;
    let val;
    if (el.dataset.type === "bool") {
      val = el.checked;
    } else if (el.dataset.type === "time-of-day") {
      val = fromTimeInput(el.value);
    } else if (el.dataset.type === "stage-workers") {
      const sid = el.dataset.stage;
      if (!liveConfig.stages[sid]) liveConfig.stages[sid] = {};
      setStageWorkers(liveConfig.stages[sid], parseInt(el.value, 10) || 0);
      if (!opts.silent) {
        markDirty(el, `stages.${sid}.workers`);
        syncLegacyInputs();
        updateJsonArea();
        window.dispatchEvent(new CustomEvent("plant-config-changed"));
      }
      return;
    } else if (el.type === "number") {
      val = el.value === "" ? null : parseFloat(el.value);
    } else {
      val = el.value;
    }
    if (path.startsWith("calendar.breaks.0.")) {
      if (!liveConfig.calendar.breaks?.length) {
        liveConfig.calendar.breaks = [{ start: "11:30", end: "12:30" }];
      }
    }
    if (path.includes(".shift.") && path.startsWith("stages.")) {
      const sid = path.split(".")[1];
      const part = path.split(".").slice(3).join(".");
      if (!liveConfig.stages[sid]) liveConfig.stages[sid] = {};
      if (!liveConfig.stages[sid].shift) liveConfig.stages[sid].shift = {};
      setByPath(liveConfig.stages[sid].shift, part, val);
    } else {
      setByPath(liveConfig, path, val);
    }
    if (!opts.silent) {
      markDirty(el, path);
      syncLegacyInputs();
      updateJsonArea();
      window.dispatchEvent(new CustomEvent("plant-config-changed"));
    }
  }

  /** Read every control into liveConfig (Run uses this; change events alone are not enough). */
  function flushToLiveConfig() {
    if (!liveConfig) return;
    const container = document.getElementById("config-editor");
    if (container) {
      container.querySelectorAll(".duration-field[data-path]").forEach((w) => {
        applyDurationField(w, { silent: true });
      });
      container.querySelectorAll("[data-path]").forEach((el) => {
        if (el.closest(".duration-field")) return;
        applyField(el, { silent: true });
      });
      refreshDirtyMarks(container);
      container.querySelectorAll(".duration-field[data-path]").forEach(markDurationDirty);
    }
    syncSimControlsToConfig();
    updateJsonArea();
    syncLegacyInputs();
  }

  function updateRunHint(effective, snapshot) {
    const hint = document.getElementById("config-run-hint");
    if (!hint) return;
    const cal = effective?.calendar || {};
    const tr = snapshot?.transfers || effective?.transfers || {};
    const sep = effective?.stages?.separation?.service_time_seconds;
    const days = snapshot?.simulation_days ?? effective?.objectives?.simulation_days;
    const parts = [
      `Last run: ${days ?? "?"} operating day(s)`,
      `plant close ${snapshot?.plant_close || cal.wash_cutoff_time || "—"}`,
      `after_separation transfer ${tr.after_separation ?? "—"} min`,
    ];
    if (sep != null) parts.push(`separation service ${sep}s`);
    const dirty =
      baselineConfig && liveConfig && !jsonEqual(baselineConfig, liveConfig);
    hint.textContent =
      parts.join(" · ") + (dirty ? " · editor differs from baseline" : " · matches baseline");
  }

  /** After a successful sim, align editor + JSON with what the server actually ran. */
  function applyEffectiveConfigAfterRun(effective, snapshot) {
    if (!effective) return;
    liveConfig = deepClone(effective);
    const container = document.getElementById("config-editor");
    if (container) renderEditor(container);
    else updateJsonArea();
    syncLegacyInputs();
    updateRunHint(effective, snapshot);
  }

  function syncLegacyInputs() {
    const days = document.getElementById("inp-sim-days");
    if (days && liveConfig?.objectives) days.value = liveConfig.objectives.simulation_days;
  }

  function refreshDirtyMarks(container) {
    container.querySelectorAll("[data-path]").forEach((el) => {
      const path = el.dataset.path;
      if (path === "stages.X._workers") return;
      if (el.dataset.type === "stage-workers") {
        const sid = el.dataset.stage;
        markDirty(el, `stages.${sid}.workers`);
        return;
      }
      markDirty(el, path);
    });
  }

  function updateJsonArea() {
    const ta = document.getElementById("inp-config-json");
    if (ta && liveConfig) ta.value = JSON.stringify(liveConfig, null, 2);
  }

  function applyJsonFromTextarea() {
    const ta = document.getElementById("inp-config-json");
    if (!ta) return;
    liveConfig = JSON.parse(ta.value);
    const container = document.getElementById("config-editor");
    if (container) renderEditor(container);
    syncLegacyInputs();
  }

  async function loadBaseline() {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    baselineConfig = await res.json();
    liveConfig = deepClone(baselineConfig);
    return liveConfig;
  }

  function resetToBaseline() {
    if (!baselineConfig) return;
    liveConfig = deepClone(baselineConfig);
    const container = document.getElementById("config-editor");
    if (container) renderEditor(container);
    updateJsonArea();
    syncLegacyInputs();
  }

  function getLiveConfig() {
    return liveConfig;
  }

  function getBaselineConfig() {
    return baselineConfig;
  }

  function loadSavedConfig(config) {
    if (!config) return;
    liveConfig = deepClone(config);
    const container = document.getElementById("config-editor");
    if (container) {
      renderEditor(container);
      refreshDirtyMarks(container);
      container.querySelectorAll(".duration-field[data-path]").forEach(markDurationDirty);
    }
    updateJsonArea();
    syncLegacyInputs();
  }

  function syncSimControlsToConfig() {
    if (!liveConfig) return;
    const days = document.getElementById("inp-sim-days");
    if (days?.value) {
      if (!liveConfig.objectives) liveConfig.objectives = {};
      liveConfig.objectives.simulation_days = parseInt(days.value, 10) || 1;
    }
  }

  window.PlantConfigEditor = {
    STAGE_IDS,
    loadBaseline,
    getLiveConfig,
    getBaselineConfig,
    loadSavedConfig,
    flushToLiveConfig,
    resetToBaseline,
    renderEditor,
    applyJsonFromTextarea,
    applyEffectiveConfigAfterRun,
    syncSimControlsToConfig,
    updateJsonArea,
    updateRunHint,
    isDirty: () => baselineConfig && liveConfig && !jsonEqual(baselineConfig, liveConfig),
  };

  document.getElementById("btn-reset-config")?.addEventListener("click", resetToBaseline);
  document.getElementById("btn-apply-json")?.addEventListener("click", () => {
    try {
      applyJsonFromTextarea();
    } catch (e) {
      alert("Invalid JSON: " + e.message);
    }
  });
})();
