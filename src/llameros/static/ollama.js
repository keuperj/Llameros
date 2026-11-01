/** Admin Ollama environment settings; explicit stopped-container recreation only. */
'use strict';
const OllamaConfig = {
  data: null,
  reset() { this.data = null; },
  render(data) {
    if (this.data) return;
    this.data = data;
    document.getElementById('ollama-keep-alive').value = data.ollama_keep_alive;
    document.getElementById('ollama-flash-attention').value = data.ollama_flash_attention ? '1' : '0';
    document.getElementById('ollama-kv-cache-type').value = data.ollama_kv_cache_type;
  },
};
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('ollama-form').addEventListener('submit', safe(async event => {
    event.preventDefault();
    if (!window.confirm('Stop all Ollama runtimes first. Recreate their containers with these environment settings? Model files are preserved. Start Ollama afterwards; restarting Llameros alone does not apply these changes.')) return;
    const data = await api('/api/ollama', {
      ollama_keep_alive: document.getElementById('ollama-keep-alive').value,
      ollama_flash_attention: document.getElementById('ollama-flash-attention').value === '1',
      ollama_kv_cache_type: document.getElementById('ollama-kv-cache-type').value,
      confirmed: true,
    });
    OllamaConfig.reset(); OllamaConfig.render(data);
    document.getElementById('ollama-feedback').textContent = 'Ollama settings saved. Start Ollama to recreate CPU/GPU containers and apply them. Model files are preserved.';
    await refresh();
  }));
});