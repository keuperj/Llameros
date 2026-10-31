/** Admin-only scheduler editor; never automatically restarts a runtime. */
'use strict';
const SchedulerConfig = {
  data: null,
  reset() { this.data = null; },
  render(data) {
    if (this.data) return;
    this.data = data;
    for (const name of ['queue_size', 'queue_timeout', 'ollama_num_parallel', 'queue_users']) document.getElementById('scheduler-' + name).value = data[name];
  },
};
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('scheduler-form').addEventListener('submit', safe(async event => {
    event.preventDefault();
    const config = {};
    for (const name of ['queue_size', 'queue_timeout', 'ollama_num_parallel', 'queue_users']) config[name] = Number(document.getElementById('scheduler-' + name).value);
    if (config.ollama_num_parallel !== SchedulerConfig.data?.ollama_num_parallel) {
      if (!window.confirm('All Ollama runtimes must be stopped. Recreate their containers with the new parallelism? Model files are preserved. Start Ollama again afterwards.')) return;
      config.confirmed = true;
    }
    const data = await api('/api/scheduler', config);
    SchedulerConfig.reset(); SchedulerConfig.render(data);
    document.getElementById('scheduler-feedback').textContent = 'Scheduler settings saved. Queue settings apply immediately. If parallelism changed, start Ollama again.';
    await refresh();
  }));
});