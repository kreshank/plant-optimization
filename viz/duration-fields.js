/**
 * Duration inputs with sec/min unit selector. Storage in config is always
 * seconds or sim-minutes depending on data-storage attribute.
 */
(function () {
  function defaultUnit(storage, stored) {
    const v = Number(stored) || 0;
    if (storage === "minutes") return v > 0 && v < 1 ? "sec" : "min";
    return v >= 90 ? "min" : "sec";
  }

  function toDisplay(stored, storage, unit) {
    const v = Number(stored) || 0;
    if (storage === "minutes") {
      return unit === "sec" ? v * 60 : v;
    }
    return unit === "min" ? v / 60 : v;
  }

  function toStored(displayVal, storage, unit) {
    const v = Number(displayVal) || 0;
    if (storage === "minutes") {
      return unit === "sec" ? v / 60 : v;
    }
    return unit === "min" ? v * 60 : v;
  }

  function formatDisplayNum(n, storage, unit) {
    if (!Number.isFinite(n)) return "0";
    const decimals = unit === "sec" && storage === "minutes" ? 1 : storage === "seconds" ? 1 : 2;
    const rounded = Math.round(n * 10 ** decimals) / 10 ** decimals;
    return String(rounded);
  }

  function rowHtml(label, path, stored, storage, opts = {}) {
    const unit = opts.unit || defaultUnit(storage, stored);
    const display = formatDisplayNum(toDisplay(stored, storage, unit), storage, unit);
    const step = unit === "sec" ? (storage === "seconds" ? "0.1" : "1") : "0.01";
    const pathAttr = path ? ` data-path="${path}"` : "";
    const simAttr = opts.simControl ? ` data-sim-control="${opts.simControl}"` : "";
    return `<label class="config-duration-label">${label}
      <span class="duration-field"${pathAttr}${simAttr} data-storage="${storage}">
        <input type="number" class="duration-value" step="${step}" min="0" value="${display}" />
        <select class="duration-unit" aria-label="Time unit">
          <option value="sec"${unit === "sec" ? " selected" : ""}>sec</option>
          <option value="min"${unit === "min" ? " selected" : ""}>min</option>
        </select>
      </span></label>`;
  }

  function syncDisplay(wrapper) {
    const storage = wrapper.dataset.storage;
    const path = wrapper.dataset.path;
    const stored = path ? getByPath(window.PlantConfigEditor?.getLiveConfig?.(), path) : wrapper.dataset.storedMinutes;
    const unitSel = wrapper.querySelector(".duration-unit");
    const input = wrapper.querySelector(".duration-value");
    if (!unitSel || !input) return;
    const unit = unitSel.value;
    const display = toDisplay(stored ?? input.dataset.storedFallback ?? 0, storage, unit);
    input.value = formatDisplayNum(display, storage, unit);
  }

  function readWrapper(wrapper) {
    const storage = wrapper.dataset.storage;
    const input = wrapper.querySelector(".duration-value");
    const unit = wrapper.querySelector(".duration-unit")?.value || "sec";
    return toStored(parseFloat(input?.value) || 0, storage, unit);
  }

  function getByPath(obj, path) {
    if (!obj || !path) return undefined;
    return path.split(".").reduce((o, k) => o?.[k], obj);
  }

  function bind(wrapper, { onChange, isDirty }) {
    const input = wrapper.querySelector(".duration-value");
    const unitSel = wrapper.querySelector(".duration-unit");
    if (!input || !unitSel) return;

    const path = wrapper.dataset.path;

    unitSel.addEventListener("change", () => {
      if (path && window.PlantConfigEditor?.getLiveConfig) {
        const stored = getByPath(window.PlantConfigEditor.getLiveConfig(), path);
        const storage = wrapper.dataset.storage;
        const display = toDisplay(stored, storage, unitSel.value);
        input.value = formatDisplayNum(display, storage, unitSel.value);
      } else if (wrapper.dataset.storedMinutes != null) {
        const storage = wrapper.dataset.storage;
        const display = toDisplay(
          parseFloat(wrapper.dataset.storedMinutes),
          storage,
          unitSel.value
        );
        input.value = formatDisplayNum(display, storage, unitSel.value);
      }
      if (onChange) onChange(wrapper);
    });

    input.addEventListener("change", () => {
      if (onChange) onChange(wrapper);
    });
    input.addEventListener("input", () => {
      if (isDirty) isDirty(wrapper);
    });
  }

  function bindAll(container, handlers) {
    container.querySelectorAll(".duration-field").forEach((w) => bind(w, handlers));
  }

  window.DurationFields = {
    rowHtml,
    defaultUnit,
    toDisplay,
    toStored,
    readWrapper,
    syncDisplay,
    bind,
    bindAll,
    formatDisplayNum,
  };
})();
