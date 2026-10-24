'use strict';
const JobProgress = {
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