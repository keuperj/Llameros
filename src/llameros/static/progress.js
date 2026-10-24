'use strict';
const JobProgress = {
  controls(runtime, busy) {
    const known = !!runtime && !runtime.error && typeof runtime.running === 'boolean';
    return {start: !busy && known && !runtime.running, stop: !busy && known && runtime.running,
      active: known && runtime.running ? (runtime.loaded || []).map(model => model.name || model.model) : []};
  },
  modelControls(runtime, busy, model) {
    const active = this.controls(runtime, busy).active;
    const ready = !busy && !!runtime && runtime.running === true && !runtime.error;
    return {load: ready && (runtime.slots ? runtime.slots.some(slot => slot.available) : active.length === 0), unload: ready && active.includes(model)};
  },
  runtimeState(runtime) {
    if (!runtime || runtime.error || typeof runtime.running !== 'boolean') return {state: 'unknown', text: 'Ollama status unavailable'};
    if (!runtime.running) return {state: 'off', text: 'Ollama off'};
    const active = this.controls(runtime, false).active;
    return active.length ? {state: 'model', text: 'Model running — ' + active.join(', ')}
      : {state: 'running', text: 'Ollama running'};
  },
  percent(progress) {
    if (['Pull complete', 'Download complete', 'Already exists'].includes(progress.status)) return 100;
    const total = Number(progress.total), done = Number(progress.completed);
    return total > 0 && Number.isFinite(done) && progress.completed != null
      ? Math.max(0, Math.min(100, done / total * 100)) : null;
  },
  availability(runtime) {
    if (runtime.error || typeof runtime.image_available !== 'boolean') return {state: 'unknown', text: 'Local availability unknown', detail: runtime.error || 'Checking Docker…'};
    if (runtime.container_exists) return {state: 'ready', text: '● Container available locally', detail: `Managed container exists${runtime.image_available ? '; configured image is local' : '; configured image tag is absent, but existing container can start'}.`};
    return runtime.image_available
      ? {state: 'ready', text: '● Image available locally', detail: `${runtime.image}: ready to create the container; no image download needed.`}
      : {state: 'missing', text: '● Image download needed', detail: `${runtime.image}: not present locally.`};
  },
};
if (typeof module !== 'undefined') module.exports = JobProgress;