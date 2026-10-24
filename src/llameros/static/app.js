'use strict';
const $ = id => document.getElementById(id);
let csrf = '', timer = null, lastResult = '', selectedFit = null, fitVersion = 0, refreshing = false;
let storageInitialized = false;
const bytes = n => n == null ? 'unknown' : `${(n / 1073741824).toFixed(1)} GiB`;
function notice(message = '') { $('notice').textContent = message; }
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') signedOut();
    throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  }
  return data;
}
function safe(fn) { return async event => { if (event) event.preventDefault(); try { await fn(event); } catch (error) { notice(error.message); } }; }
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
function options(select, values, placeholder) {
  select.replaceChildren(); if (placeholder) select.add(new Option(placeholder, ''));
  values.forEach(value => select.add(new Option(value, value)));
}
function signedOut() { csrf = ''; storageInitialized = false; clearInterval(timer); timer = null; $('dashboard').hidden = true; $('login-panel').hidden = false; $('logout').hidden = true; $('identity').textContent = ''; }
async function signedIn(data) {
  csrf = data.csrf; $('identity').textContent = data.username; $('dashboard').hidden = false;
  $('login-panel').hidden = true; $('logout').hidden = false; notice();
  clearInterval(timer); timer = setInterval(safe(refresh), 5000);
  await refresh(); await catalogue();
}
$('login-form').addEventListener('submit', safe(async () => {
  try { const data = await api('/api/login', {username: $('username').value, password: $('password').value}); await signedIn(data); }
  finally { $('password').value = ''; }
}));
$('logout').addEventListener('click', safe(async () => { await api('/api/logout', {}); signedOut(); }));
$('refresh').addEventListener('click', safe(refresh));
$('storage-form').addEventListener('submit', safe(async () => {
  await api('/api/storage', {directory: $('storage-directory').value.trim(), confirmed: $('storage-confirm').checked});
  $('storage-feedback').textContent = 'Storage change queued; see the runtime job for completion or errors.';
  $('storage-confirm').checked = false;
  await refresh();
}));
for (const action of ['start', 'stop']) $('container-' + action).addEventListener('click', safe(async () => { await api('/api/container/' + action, {}); await refresh(); }));
async function catalogue() {
  const data = await api('/api/catalog?q=' + encodeURIComponent($('search').value));
  options($('families'), data.models, 'Select a family');
}
$('search-form').addEventListener('submit', safe(catalogue));
$('families').addEventListener('change', safe(async () => {
  fitVersion++; selectedFit = null; $('pull').disabled = true; options($('tags'), [], 'Loading variants…');
  const family = $('families').value; if (!family) return;
  const data = await api('/api/catalog/' + encodeURIComponent(family) + '/tags');
  if ($('families').value === family) options($('tags'), data.tags, 'Select a variant');
}));
$('tags').addEventListener('change', safe(async () => {
  const version = ++fitVersion, model = $('tags').value; selectedFit = null; $('pull').disabled = true;
  if (!model) return; $('fit').textContent = 'Checking registry and available memory…';
  const data = await api('/api/fit?model=' + encodeURIComponent(model));
  if (version !== fitVersion) return;
  selectedFit = data; $('pull').disabled = !data.fits;
  $('fit').textContent = `${data.fits ? '✓ Estimated to fit' : '✕ Insufficient memory'} · ${bytes(data.download_bytes)} download · ${bytes(data.estimated_memory)} estimated runtime · ${data.placement}. ${data.note}`;
}));
$('pull').addEventListener('click', safe(async () => {
  if (!selectedFit || !selectedFit.fits) return;
  await api('/api/models/pull', {model: selectedFit.model}); await refresh();
}));
$('generate-form').addEventListener('submit', safe(async () => {
  await api('/api/generate', {model: $('generate-model').value, prompt: $('prompt').value, max_tokens: 256});
  $('output').textContent = 'Generating…'; await refresh();
}));
async function refresh() {
  if (refreshing || !csrf) return; refreshing = true;
  try {
    const [data, history, inventory] = await Promise.all([api('/api/status'), api('/api/history'), api('/api/models')]);
    const storage = inventory.storage;
    $('storage-current').textContent = `Active location: ${storage.location} (${storage.mode === 'directory' ? 'host directory' : 'Docker volume'})`;
    if (!storageInitialized) { $('storage-directory').value = storage.directory; $('storage-directory').placeholder = `Empty = Docker volume; example: ${storage.suggested_directory}`; storageInitialized = true; }
    const hw = data.hardware;
    $('hardware').replaceChildren();
    for (const [label, value, detail] of [
      ['CPU', hw.cpu_percent.toFixed(0) + '%', `${hw.logical_cores} threads · ${hw.cpu}`],
      ['RAM AVAILABLE', bytes(hw.memory_available), `${bytes(hw.memory_total)} total`],
      ['GPU', hw.gpus.length ? hw.gpus.map(g => g.vendor).join(', ') : 'CPU mode', hw.gpus.map(g => `${g.name}: ${bytes(g.free)} free`).join(' · ') || 'No supported GPU detected'],
      ['RUNTIME', data.ollama.running ? 'Online' : 'Offline', `${data.gpu_mode} · ${data.context_length} context`],
    ]) { const card = element('div', '', 'metric'); card.append(element('small', label), element('strong', value), element('small', detail)); $('hardware').append(card); }
    $('runtime').textContent = data.ollama.running ? 'RUNNING' : 'STOPPED';
    const resource = data.ollama.resources;
    $('runtime-detail').textContent = data.ollama.error || (resource ? `Container CPU ${resource.CPUPerc} · memory ${resource.MemUsage} · network ${resource.NetIO}` : 'Start the managed container to download or run models.');
    const job = data.job;
    $('storage-save').disabled = data.ollama.running || job.state === 'running';
    if (job.action === 'storage-change' && job.state !== 'running') $('storage-feedback').textContent = job.state === 'completed' ? 'Storage location saved. Start Ollama to use it. Existing models have not been moved or deleted.' : job.error;
    $('job').textContent = job.state === 'idle' ? '' : `${job.action}: ${job.state}${job.model ? ' · ' + job.model : ''}${job.progress ? ' · ' + JSON.stringify(job.progress) : ''}${job.error ? ' · ' + job.error : ''}`;
    const resultKey = job.started + ':' + job.state;
    if (job.action === 'generate' && job.state === 'completed' && resultKey !== lastResult) {
      lastResult = resultKey; const r = job.result;
      $('output').textContent = (r.thinking ? '[Thinking]\n' + r.thinking + '\n\n' : '') + (r.response || '') + `\n\n${r.eval_count || 0} tokens · ${((r.total_duration || 0) / 1e9).toFixed(2)} seconds`;
    }
    if (job.action === 'generate' && job.state === 'failed') $('output').textContent = job.error;
    const models = inventory.models || [], loaded = data.ollama.loaded || [];
    $('model-count').textContent = models.length;
    $('inventory-detail').textContent = `${inventory.source === 'live' ? 'Live Ollama inventory' : inventory.source === 'disk' ? 'Read from model directory; blob sizes checked (not checksums)' : inventory.source === 'cache' ? 'Last known inventory from ' + new Date(inventory.cached_at * 1000).toLocaleString() + ' — start Ollama to refresh' : 'Inventory unavailable until Ollama starts'}. ${(inventory.warnings || []).join(' ')}`;
    const current = $('generate-model').value; options($('generate-model'), models.map(m => m.name)); if (models.some(m => m.name === current)) $('generate-model').value = current;
    $('generate-form').querySelector('button').disabled = !data.ollama.running || !models.length || job.state === 'running';
    $('installed').replaceChildren();
    if (!models.length) $('installed').append(element('p', ['unknown', 'unavailable'].includes(inventory.source) ? 'No inventory available. Start Ollama to check this storage location.' : 'No downloaded models found in the selected storage location.'));
    models.forEach(model => {
      const row = element('div', '', 'model'), active = loaded.some(m => m.name === model.name);
      row.append(element('strong', model.name), element('small', `${bytes(model.size)}${active ? ' · loaded' : ' · downloaded'}${model.modified_at ? ' · ' + new Date(model.modified_at).toLocaleDateString() : ''}`));
      const actions = element('div', '', 'actions');
      for (const action of ['load', 'unload']) { const button = element('button', action === 'load' ? 'Load / start' : 'Unload / stop'); button.disabled = job.state === 'running' || !data.ollama.running; button.addEventListener('click', safe(async () => { await api('/api/models/' + action, {model: model.name}); await refresh(); })); actions.append(button); }
      row.append(actions); $('installed').append(row);
    });
    $('usage').replaceChildren();
    history.models.forEach(model => { const row = element('tr', ''); [model.model, model.requests, model.prompt_tokens, model.output_tokens, model.tokens_per_second.toFixed(1), model.seconds.toFixed(1)].forEach(value => row.append(element('td', value))); $('usage').append(row); });
    $('events').replaceChildren(); history.events.slice(0, 20).forEach(event => $('events').append(element('div', `${new Date(event.timestamp * 1000).toLocaleString()} · ${event.kind} · ${event.model} ${event.detail}`, 'event')));
    draw(history.samples);
  } finally { refreshing = false; }
}
function draw(samples) {
  const canvas = $('chart'), ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h); ctx.strokeStyle = '#303e35'; ctx.fillStyle = '#a1b2a6'; ctx.font = '12px system-ui';
  for (const pct of [0, 50, 100]) { const y = 15 + (100 - pct) / 100 * (h - 35); ctx.beginPath(); ctx.moveTo(40, y); ctx.lineTo(w, y); ctx.stroke(); ctx.fillText(pct + '%', 0, y + 4); }
  if (!samples.length) { ctx.fillText('Resource history appears after the first sample.', 60, 90); return; }
  for (const [key, color] of [['cpu_percent', '#bcf48c'], ['memory_percent', '#ffbc86']]) {
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
    samples.forEach((sample, i) => { const x = 42 + i / Math.max(1, samples.length - 1) * (w - 45), y = 15 + (100 - sample.hardware[key]) / 100 * (h - 35); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
  }
}
safe(async () => { try { const data = await api('/api/session'); await signedIn(data); } catch (error) { if (csrf) notice(error.message); else signedOut(); } })();