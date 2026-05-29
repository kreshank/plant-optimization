/**
 * Named plant config scenarios (browser localStorage + JSON export/import).
 */
(function () {
  const STORAGE_KEY = "plant_viz_config_presets_v1";
  const MAX_DIFF_LINES = 12;

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function jsonEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function loadStore() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { version: 1, presets: [] };
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.presets)) return { version: 1, presets: [] };
      return data;
    } catch {
      return { version: 1, presets: [] };
    }
  }

  function saveStore(store) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  }

  function newId() {
    return `p_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  }

  function diffPaths(baseline, current, prefix = "") {
    const paths = [];
    if (jsonEqual(baseline, current)) return paths;
    const bIsObj =
      baseline !== null && typeof baseline === "object" && !Array.isArray(baseline);
    const cIsObj =
      current !== null && typeof current === "object" && !Array.isArray(current);
    if (!bIsObj || !cIsObj || Array.isArray(baseline) || Array.isArray(current)) {
      if (prefix) paths.push(prefix);
      return paths;
    }
    const keys = new Set([...Object.keys(baseline), ...Object.keys(current)]);
    for (const k of keys) {
      const p = prefix ? `${prefix}.${k}` : k;
      paths.push(...diffPaths(baseline[k], current[k], p));
    }
    return paths;
  }

  function summarizeDiff(paths) {
    if (!paths.length) return "Same as baseline";
    if (paths.length <= MAX_DIFF_LINES) return paths.join(", ");
    return `${paths.slice(0, MAX_DIFF_LINES).join(", ")} … (+${paths.length - MAX_DIFF_LINES} more)`;
  }

  function formatWhen(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso || "";
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function getEditor() {
    return window.PlantConfigEditor;
  }

  function buildPresetRecord(name, config, baseline) {
    const now = new Date().toISOString();
    const diff = baseline ? diffPaths(baseline, config) : [];
    return {
      id: newId(),
      name: name.trim(),
      createdAt: now,
      updatedAt: now,
      config: deepClone(config),
      diffPaths: diff,
      diffSummary: summarizeDiff(diff),
    };
  }

  function saveCurrentPreset(name) {
    const ed = getEditor();
    if (!ed) throw new Error("Config editor not ready");
    const trimmed = (name || "").trim();
    if (!trimmed) throw new Error("Enter a scenario name");
    ed.flushToLiveConfig?.();
    const config = ed.getLiveConfig();
    if (!config) throw new Error("No config loaded");
    const baseline = ed.getBaselineConfig?.();
    const store = loadStore();
    const existing = store.presets.find(
      (p) => p.name.toLowerCase() === trimmed.toLowerCase()
    );
    if (existing) {
      existing.config = deepClone(config);
      existing.updatedAt = new Date().toISOString();
      existing.diffPaths = baseline ? diffPaths(baseline, config) : [];
      existing.diffSummary = summarizeDiff(existing.diffPaths);
    } else {
      store.presets.unshift(buildPresetRecord(trimmed, config, baseline));
    }
    store.presets.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
    saveStore(store);
    return existing ? existing.id : store.presets[0].id;
  }

  function loadPresetById(id) {
    const ed = getEditor();
    const preset = loadStore().presets.find((p) => p.id === id);
    if (!preset || !ed) return false;
    ed.loadSavedConfig(preset.config);
    return true;
  }

  function deletePreset(id) {
    const store = loadStore();
    const next = store.presets.filter((p) => p.id !== id);
    if (next.length === store.presets.length) return false;
    store.presets = next;
    saveStore(store);
    return true;
  }

  function exportOnePreset(id) {
    const preset = loadStore().presets.find((p) => p.id === id);
    if (!preset) return;
    const blob = new Blob(
      [
        JSON.stringify(
          {
            format: "plant-viz-preset",
            version: 1,
            name: preset.name,
            savedAt: preset.updatedAt || preset.createdAt,
            diffSummary: preset.diffSummary,
            config: preset.config,
          },
          null,
          2
        ),
      ],
      { type: "application/json" }
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `plant-scenario-${preset.name.replace(/[^\w.-]+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function exportAllPresets() {
    const store = loadStore();
    const blob = new Blob([JSON.stringify(store, null, 2)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "plant-scenarios.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function importPresetsFromFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const data = JSON.parse(reader.result);
          const incoming = [];
          if (data?.format === "plant-viz-preset" && data.config) {
            incoming.push({
              name: data.name || "Imported",
              config: data.config,
            });
          } else if (Array.isArray(data?.presets)) {
            for (const p of data.presets) {
              if (p?.config) incoming.push({ name: p.name || "Imported", config: p.config });
            }
          } else {
            reject(new Error("Unrecognized file format"));
            return;
          }
          const ed = getEditor();
          const baseline = ed?.getBaselineConfig?.();
          const store = loadStore();
          for (const row of incoming) {
            const rec = buildPresetRecord(row.name, row.config, baseline);
            const dup = store.presets.find(
              (p) => p.name.toLowerCase() === rec.name.toLowerCase()
            );
            if (dup) {
              dup.config = rec.config;
              dup.updatedAt = rec.updatedAt;
              dup.diffPaths = rec.diffPaths;
              dup.diffSummary = rec.diffSummary;
            } else {
              store.presets.push(rec);
            }
          }
          store.presets.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
          saveStore(store);
          resolve(incoming.length);
        } catch (e) {
          reject(e);
        }
      };
      reader.onerror = () => reject(reader.error || new Error("Read failed"));
      reader.readAsText(file);
    });
  }

  function renderPresetList() {
    const list = document.getElementById("config-preset-list");
    if (!list) return;
    const ed = getEditor();
    const baseline = ed?.getBaselineConfig?.();
    ed?.flushToLiveConfig?.();
    const live = ed?.getLiveConfig?.();
    const store = loadStore();

    if (!store.presets.length) {
      list.innerHTML = '<li class="config-preset-empty">No saved scenarios yet.</li>';
      return;
    }

    list.innerHTML = store.presets
      .map((p) => {
        const active = live && jsonEqual(live, p.config);
        const diff =
          p.diffSummary ||
          summarizeDiff(
            p.diffPaths || (baseline ? diffPaths(baseline, p.config) : [])
          );
        return `<li class="config-preset-item${active ? " is-active" : ""}" data-id="${escapeHtml(p.id)}">
          <div class="config-preset-head">
            <strong>${escapeHtml(p.name)}</strong>
            ${active ? '<span class="config-preset-badge">loaded</span>' : ""}
          </div>
          <div class="config-preset-meta">${escapeHtml(formatWhen(p.updatedAt || p.createdAt))}</div>
          <div class="config-preset-diff" title="${escapeHtml(diff)}">vs baseline: ${escapeHtml(diff)}</div>
          <div class="config-preset-actions">
            <button type="button" class="legend-btn btn-preset-load" data-id="${escapeHtml(p.id)}">Load</button>
            <button type="button" class="legend-btn btn-preset-export" data-id="${escapeHtml(p.id)}">Export</button>
            <button type="button" class="legend-btn btn-preset-delete" data-id="${escapeHtml(p.id)}">Delete</button>
          </div>
        </li>`;
      })
      .join("");
  }

  function wireUi() {
    const saveBtn = document.getElementById("btn-save-preset");
    const nameInput = document.getElementById("inp-preset-name");
    const exportAllBtn = document.getElementById("btn-export-presets");
    const importInput = document.getElementById("inp-import-presets");
    const list = document.getElementById("config-preset-list");

    saveBtn?.addEventListener("click", () => {
      try {
        saveCurrentPreset(nameInput?.value || "");
        if (nameInput) nameInput.value = "";
        renderPresetList();
      } catch (e) {
        alert(e.message);
      }
    });

    nameInput?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") saveBtn?.click();
    });

    exportAllBtn?.addEventListener("click", exportAllPresets);

    importInput?.addEventListener("change", async () => {
      const file = importInput.files?.[0];
      importInput.value = "";
      if (!file) return;
      try {
        const n = await importPresetsFromFile(file);
        renderPresetList();
        alert(n === 1 ? "Imported 1 scenario." : `Imported ${n} scenarios.`);
      } catch (e) {
        alert(`Import failed: ${e.message}`);
      }
    });

    list?.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-id]");
      if (!btn) return;
      const id = btn.dataset.id;
      if (btn.classList.contains("btn-preset-load")) {
        if (loadPresetById(id)) renderPresetList();
        return;
      }
      if (btn.classList.contains("btn-preset-export")) {
        exportOnePreset(id);
        return;
      }
      if (btn.classList.contains("btn-preset-delete")) {
        if (confirm("Delete this saved scenario?")) {
          deletePreset(id);
          renderPresetList();
        }
      }
    });
  }

  window.PlantConfigPresets = {
    saveCurrentPreset,
    loadPresetById,
    deletePreset,
    renderPresetList,
    exportAllPresets,
    diffPaths,
  };

  wireUi();
})();
