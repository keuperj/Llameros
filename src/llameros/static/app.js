'use strict';
const $ = id => document.getElementById(id);
let csrf = '', timer = null, lastResult = '', selectedFit = null, fitVersion = 0, refreshing = false;
let storageInitialized = false;
let catalogueVersion = 0, familyVersion = 0, catalogueEntries = [];
let loadedCatalogueQuery = '';
let busy = false, jobTimer = null, pollingJob = false;
function progressBar(label, value) {
  const wrapper = element('div', '', 'progress-row');
  wrapper.append(element('div', label));
  const bar = document.createElement('progress'); bar.max = 100; bar.setAttribute('aria-label', label);
  if (value !== null) bar.value = value;
  wrapper.append(bar); return wrapper;
}
function renderJob(job) {
  busy = job.state === 'running';
  $('job').replaceChildren();
  if (job.state !== 'idle') {
    const p = job.progress || {}, value = job.state === 'completed' ? 100 : JobProgress.percent(p);
    const elapsed = Math.max(0, Math.round((job.finished || Date.now() / 1000) - job.started));
    $('job').append(element('strong', `${job.action} · ${job.state}${job.model ? ' · ' + job.model : ''} · ${elapsed}s`));
    if (job.state === 'failed') $('job').append(element('p', job.error || 'Operation failed'));
    else $('job').append(progressBar(`${p.status || 'Working…'}${value !== null ? ' · ' + value.toFixed(0) + '%' : ' · progress percentage unavailable'}`, value));
    if (busy) for (const layer of p.layers || []) {
      const percent = JobProgress.percent(layer), id = layer.id || layer.digest || '';
      $('job').append(progressBar(`${id.slice(0, 20)} · ${layer.status || 'Downloading'}${percent === null ? '' : ' · ' + percent.toFixed(0) + '% · ' + bytes(layer.completed) + ' / ' + bytes(layer.total)}`, percent));
    }
  }
  $('container-start').disabled = busy;
  $('container-stop').disabled = busy;
  $('pull').disabled = busy || !selectedFit || !selectedFit.fits;
  if (busy) document.querySelectorAll('#installed button, #generate-form button, #storage-save').forEach(button => { button.disabled = true; });
}
async function pollJob() {
  if (!csrf || pollingJob) return;
  pollingJob = true;
  try { const job = await api('/api/job'); if (csrf) renderJob(job); } catch (error) { notice(error.message); }
  finally { pollingJob = false; }
}
const cpuOnly = () => $('cpu-only').checked;
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
function updateModelInfo() {
  const link = $('model-info');
  const url = Catalogue.modelInfoUrl($('tags').value || $('families').value);
  if (url) { link.href = url; link.removeAttribute('aria-disabled'); link.removeAttribute('tabindex'); }
  else { link.removeAttribute('href'); link.setAttribute('aria-disabled', 'true'); link.setAttribute('tabindex', '-1'); }
}
function signedOut() { csrf = ''; catalogueVersion++; familyVersion++; fitVersion++; storageInitialized = false; $('config-menu').open = false; clearInterval(timer); clearInterval(jobTimer); jobTimer = null; timer = null; $('dashboard').hidden = true; $('login-panel').hidden = false; $('logout').hidden = true; $('identity').textContent = ''; }
async function signedIn(data) {
  csrf = data.csrf; $('identity').textContent = data.username; $('dashboard').hidden = false;
  $('login-panel').hidden = true; $('logout').hidden = false; notice();
  clearInterval(timer); timer = setInterval(safe(refresh), 5000); clearInterval(jobTimer); jobTimer = setInterval(pollJob, 1000);
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
  const version = ++catalogueVersion;
  familyVersion++; fitVersion++; selectedFit = null; $('pull').disabled = true;
  options($('tags'), [], 'Select a variant');
  $('families').value = ''; updateModelInfo();
  const query = $('search').value.trim(), cpu = cpuOnly();
  $('catalog-progress').textContent = 'Searching Ollama…';
  const data = await api('/api/catalog?q=' + encodeURIComponent(query));
  if (version !== catalogueVersion) return;
  loadedCatalogueQuery = query;
  $('families').value = '';
  catalogueEntries = data.models.map((name, rank) => ({name, rank, fits: null, variants: []}));
  let next = 0, checked = 0, failures = 0;
  const render = () => {
    renderFamilies(); renderRecommendations();
    const visible = Catalogue.visible(catalogueEntries, loadedCatalogueQuery, cpu);
    $('catalog-progress').textContent = `Checked ${checked}/${catalogueEntries.length} families${failures ? ` · ${failures} unavailable` : ''} · ${visible.length} shown${cpu ? ' · CPU-only RAM fit filter' : ''}. ${checked < catalogueEntries.length ? 'Checking in popularity order…' : 'No results? Try another family or tag, clear CPU-only to see oversized/unknown variants, or free RAM and recheck.'}`;
  };
  render();
  async function worker() {
    while (version === catalogueVersion && csrf && next < catalogueEntries.length) {
      const entry = catalogueEntries[next++];
      try {
        const result = await api('/api/catalog/' + encodeURIComponent(entry.name) + '/compatibility?cpu_only=' + cpu);
        if (version !== catalogueVersion) return;
        Object.assign(entry, {fits: result.fits, variants: result.variants});
      } catch (error) { if (version !== catalogueVersion) return; failures++; }
      checked++; render();
    }
  }
  // Bound network concurrency; no registry request per tag or blocking full-catalogue scan.
  await Promise.all(Array.from({length: 3}, worker));
}
function compatibleOptions(select, entries, placeholder) {
  const current = select.value;
  select.replaceChildren(new Option(placeholder, ''));
  for (const entry of entries) {
    const option = new Option(Catalogue.label(entry), entry.name);
    if (entry.fits === true) option.className = 'fit-option';
    select.add(option);
  }
  if (entries.some(entry => entry.name === current)) select.value = current;
}
function renderFamilies() {
  compatibleOptions($('families'), Catalogue.sort(Catalogue.visible(catalogueEntries, loadedCatalogueQuery, cpuOnly())), 'Select a family');
  updateModelInfo();
}
function renderRecommendations() {
  $('recommended').replaceChildren();
  for (const entry of Catalogue.recommendations(Catalogue.visible(catalogueEntries, loadedCatalogueQuery, cpuOnly()))) {
    const row = element('div', '', 'recommendation');
    const bullet = element('span', '●', 'fit-bullet'); bullet.setAttribute('aria-hidden', 'true');
    const button = element('button', entry.variant.name);
    button.addEventListener('click', safe(async () => {
      renderFamilies(); $('families').value = entry.family;
      await selectFamily(entry.variant.name);
    }));
    row.append(bullet, button, element('small', `~${bytes(entry.variant.download_bytes)} · popularity rank ${entry.rank + 1}`));
    $('recommended').append(row);
  }
  if (!$('recommended').children.length) $('recommended').append(element('p', 'No compatible recommendations found yet. Unknown or oversized variants are not recommended.'));
}
$('search').addEventListener('input', () => {
  // Do not pretend a local substring filter is an upstream catalogue search.
  catalogueVersion++; familyVersion++; fitVersion++; selectedFit = null; $('pull').disabled = true;
  $('catalog-progress').textContent = 'Press Search / recheck to search Ollama for this query.';
});
$('cpu-only').addEventListener('change', safe(catalogue));
$('search-form').addEventListener('submit', safe(catalogue));
async function selectFamily(preferred = '') {
  const version = ++familyVersion;
  fitVersion++; selectedFit = null; $('pull').disabled = true; options($('tags'), [], 'Loading variants…');
  updateModelInfo();
  $('fit').textContent = 'Select a variant for an exact registry size and current memory check.';
  const family = $('families').value; if (!family) { options($('tags'), [], 'Select a variant'); return; }
  const cpu = cpuOnly();
  const data = await api('/api/catalog/' + encodeURIComponent(family) + '/compatibility?cpu_only=' + cpu);
  if (version !== familyVersion || $('families').value !== family) return;
  const variants = data.variants.filter(v => (!cpu || v.cpu_fits === true) && (!loadedCatalogueQuery.includes(':') || v.name.toLowerCase().includes(loadedCatalogueQuery.toLowerCase())));
  compatibleOptions($('tags'), variants, 'Select a variant');
  updateModelInfo();
  if (preferred && variants.some(v => v.name === preferred)) { $('tags').value = preferred; await checkSelectedFit(); }
}
$('families').addEventListener('change', safe(() => selectFamily()));
async function checkSelectedFit() {
  updateModelInfo();
  const version = ++fitVersion, model = $('tags').value; selectedFit = null; $('pull').disabled = true;
  if (!model) return; $('fit').textContent = 'Checking registry and available memory…';
  const data = await api('/api/fit?model=' + encodeURIComponent(model) + '&cpu_only=' + cpuOnly());
  if (version !== fitVersion) return;
  selectedFit = data; $('pull').disabled = busy || !data.fits;
  $('fit').textContent = `${data.fits ? '✓ Estimated to fit' : '✕ Insufficient memory'} · ${bytes(data.download_bytes)} download · ${bytes(data.estimated_memory)} estimated runtime · ${data.placement}. ${data.note}`;
}
$('tags').addEventListener('change', safe(checkSelectedFit));
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
    const available = JobProgress.availability(data.ollama);
    $('image-availability').textContent = available.text; $('image-availability').title = available.detail; $('image-availability').dataset.state = available.state;
    const job = data.job;
    $('storage-save').disabled = data.ollama.running || job.state === 'running';
    if (job.action === 'storage-change' && job.state !== 'running') $('storage-feedback').textContent = job.state === 'completed' ? 'Storage location saved. Start Ollama to use it. Existing models have not been moved or deleted.' : job.error;
    renderJob(job);
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
