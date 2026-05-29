/**
 * Branch-aware recording buffer for streamed sim batches.
 */
(function () {
  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function isObj(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  function applyDelta(base, delta) {
    const result = deepClone(base);
    for (const key of Object.keys(delta)) {
      const val = delta[key];
      if (isObj(result[key]) && isObj(val)) {
        result[key] = applyDelta(result[key], val);
      } else {
        result[key] = deepClone(val);
      }
    }
    return result;
  }

  function decodeStep(prev, step) {
    const mode = step.mode || "absolute";
    const payload = step.snapshot || {};
    if (mode === "absolute" || !prev) return deepClone(payload);
    return applyDelta(prev, payload);
  }

  class Recording {
    constructor() {
      this.samples = [];
      this.flowEvents = [];
      this.checkpoints = {};
      this.activeBranchId = 0;
      this.forkIndex = -1;
      this.generation = 0;
      this.layout = null;
      this._prevAbsolute = null;
    }

    reset() {
      this.samples = [];
      this.flowEvents = [];
      this.checkpoints = {};
      this._prevAbsolute = null;
    }

    applyInit(payload) {
      if (payload.groups) this.layout = payload;
      if (payload.branch_id != null) this.activeBranchId = payload.branch_id;
    }

    truncateAfter(k) {
      this.forkIndex = k;
      if (k < 0) return;
      this.samples = this.samples.filter((s) => s.i <= k);
      const tCut = this.samples[k]?.t ?? Infinity;
      this.flowEvents = this.flowEvents.filter((e) => e.t <= tCut + 0.001);
      if (this.samples[k]) {
        const { i, t, branchId, ...rest } = this.samples[k];
        this._prevAbsolute = deepClone(rest);
      }
    }

    validMaxIndex() {
      if (!this.samples.length) return -1;
      for (let i = 0; i < this.samples.length; i++) {
        if (this.samples[i].i !== i) return i - 1;
      }
      return this.samples.length - 1;
    }

    onFork(payload) {
      const k = payload.fork_index;
      if (k != null && k >= 0) this.truncateAfter(k);
      if (payload.branch_id != null) this.activeBranchId = payload.branch_id;
      if (payload.generation != null) this.generation = payload.generation;
    }

    applyBatch(batch) {
      if (batch.generation != null && batch.generation < this.generation) return;
      if (batch.branch_id != null && batch.branch_id < this.activeBranchId) return;
      if (batch.generation != null) this.generation = batch.generation;
      if (batch.branch_id != null) this.activeBranchId = batch.branch_id;

      const steps = batch.steps || [];
      for (const step of steps) {
        const i = step.i;
        if (this.forkIndex >= 0 && i > this.forkIndex) {
          this.samples = this.samples.filter((s) => s.i !== i || s.i <= this.forkIndex);
        }
        const abs = decodeStep(this._prevAbsolute, step);
        this._prevAbsolute = abs;
        const row = { i, t: step.t, branchId: this.activeBranchId, ...abs };
        const idx = this.samples.findIndex((s) => s.i === i);
        if (idx >= 0) this.samples[idx] = row;
        else this.samples.push(row);
      }
      this.samples.sort((a, b) => a.i - b.i);

      if (batch.flow_events?.length) {
        const tCut =
          this.forkIndex >= 0 && this.samples[this.forkIndex]
            ? this.samples[this.forkIndex].t + 0.001
            : -Infinity;
        const fe = batch.flow_events.filter((e) => e.t > tCut);
        if (fe.length) this.flowEvents.push(...fe);
        this.flowEvents.sort((a, b) => (a.t || 0) - (b.t || 0));
      }
    }

    toTimeSeries(intervalMinutes) {
      return {
        interval_minutes: intervalMinutes,
        samples: this.samples.map((s) => {
          const { i, branchId, ...rest } = s;
          return { t: s.t, ...rest };
        }),
        sample_count: this.samples.length,
      };
    }

    toExportGraph(state, intervalMinutes, meta = {}) {
      const ts = this.toTimeSeries(intervalMinutes);
      const maxI = this.validMaxIndex();
      const partial =
        !!meta.streaming ||
        (maxI >= 0 && maxI + 1 < (ts.samples?.length || 0)) ||
        this.forkIndex >= 0;
      return {
        format: "plant-viz-recording",
        version: 1,
        exportedAt: new Date().toISOString(),
        partial,
        branch_id: this.activeBranchId,
        fork_index: this.forkIndex,
        generation: this.generation,
        branch_fidelity_note: meta.branchFidelityNote ?? null,
        groups: state.groups || this.layout?.groups || [],
        group_links: state.group_links || this.layout?.group_links || [],
        layout_meta: state.layout_meta || this.layout?.layout_meta || {},
        edges: state.edges || [],
        time_series: ts,
        flow_events: this.flowEvents,
        summary: state.summary || {},
        block_statistics: state.block_statistics || {},
        effective_config: state.effective_config || this.layout?.effective_config || null,
        config_snapshot: state.config_snapshot || null,
        seed: meta.seed ?? null,
        sample_interval_minutes: intervalMinutes,
        memo_key: meta.memoKey ?? null,
      };
    }
  }

  window.SimRecording = Recording;
})();
