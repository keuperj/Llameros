/**
 * @file Pure runtime availability and progress presentation for the Llameros dashboard.
 * Unknown runtime state must not enable lifecycle controls.
 */
'use strict';
const JobProgress = {
  /**
   * Determine allowed container actions only from a known runtime status and idle job state.
   *
   * @param {?Object} runtime - Current snapshot or unknown state.
   * @param {boolean} busy - Whether conflicting lifecycle work is active.
   * @returns {{start: boolean, stop: boolean, active: string[]}} Button state and resident names.
   */
  controls(runtime, busy) {
    const known = !!runtime && !runtime.error && typeof runtime.running === 'boolean';
    return {start: !busy && known && !runtime.running, stop: !busy && known && runtime.running,
      active: known && runtime.running ? (runtime.loaded || []).map(model => model.name || model.model) : []};
  },
  /**
   * Determine load/unload availability from known residency and available hardware slots.
   *
   * @param {?Object} runtime - Current runtime snapshot.
   * @param {boolean} busy - Whether lifecycle controls are reserved.
   * @param {string} model - Candidate model name.
   * @returns {{load: boolean, unload: boolean}} Allowed model actions.
   */
  modelControls(runtime, busy, model) {
    const active = this.controls(runtime, busy).active;
    const ready = !busy && !!runtime && runtime.running === true && !runtime.error;
    return {load: ready && !active.includes(model) && (runtime.slots ? runtime.slots.some(slot => slot.available) : active.length === 0), unload: ready && active.includes(model)};
  },
  /**
   * Describe unknown, stopped, empty-running, and model-resident states distinctly.
   *
   * @param {?Object} runtime - Current runtime snapshot.
   * @returns {{state: string, text: string}} Display state.
   */
  runtimeState(runtime) {
    if (!runtime || runtime.error || typeof runtime.running !== 'boolean') return {state: 'unknown', text: 'Ollama status unavailable'};
    if (!runtime.running) return {state: 'off', text: 'Ollama off'};
    const active = this.controls(runtime, false).active;
    return active.length ? {state: 'model', text: 'Model running'}
      : {state: 'running', text: 'Ollama running'};
  },
  /**
   * Convert layer progress to a clamped percentage, preserving unknown totals as null.
   *
   * @param {Object} progress - Download status and byte counters.
   * @returns {?number} Percentage from 0 to 100, or null for indeterminate progress.
   */
  percent(progress) {
    if (['Pull complete', 'Download complete', 'Already exists'].includes(progress.status)) return 100;
    const total = Number(progress.total), done = Number(progress.completed);
    return total > 0 && Number.isFinite(done) && progress.completed != null
      ? Math.max(0, Math.min(100, done / total * 100)) : null;
  },
  /**
   * Distinguish local image/container readiness from missing and unknown daemon state.
   *
   * @param {Object} runtime - Runtime/image availability metadata.
   * @returns {{state: string, text: string, detail: string}} Availability presentation.
   */
  availability(runtime) {
    if (runtime.error || typeof runtime.image_available !== 'boolean') return {state: 'unknown', text: 'Local availability unknown', detail: runtime.error || 'Checking Docker…'};
    if (runtime.container_exists) return {state: 'ready', text: '● Container available locally', detail: `Managed container exists${runtime.image_available ? '; configured image is local' : '; configured image tag is absent, but existing container can start'}.`};
    return runtime.image_available
      ? {state: 'ready', text: '● Image available locally', detail: `${runtime.image}: ready to create the container; no image download needed.`}
      : {state: 'missing', text: '● Image download needed', detail: `${runtime.image}: not present locally.`};
  },
};
if (typeof module !== 'undefined') module.exports = JobProgress;