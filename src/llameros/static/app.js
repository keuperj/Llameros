/**
 * @file Llameros dashboard controller: session lifecycle, API calls, model/key views, and telemetry.
 * Uses the helper scripts loaded by index.html; server-side checks remain authoritative.
 */
'use strict';
/** @param {string} id - Dashboard element ID. @returns {?HTMLElement} Matching page element. */
const $ = id => document.getElementById(id);
let csrf = '', timer = null, lastResult = '', selectedFit = null, fitVersion = 0, refreshing = false;
let storageInitialized = false;
let catalogueVersion = 0, familyVersion = 0, catalogueEntries = [];
let loadedCatalogueQuery = '';
let busy = false, jobTimer = null, pollingJob = false;
let runtimeState = null, infoVersion = 0;
let accessConfig = null, accessContext = 4096;
let sessionEpoch = 0;
/** Invalidate all outstanding account/board requests and erase private presentation. */
function clearSessionView() {
  if (typeof TokenLimits !== 'undefined') TokenLimits.reset();
  sessionEpoch++;
  lastResult = ''; selectedFit = null; catalogueEntries = []; loadedCatalogueQuery = '';
  runtimeState = null; accessConfig = null; storageInitialized = false;
  busy = false; refreshing = false; pollingJob = false;
  catalogueVersion++; familyVersion++; fitVersion++; infoVersion++;
  localActionEvents.length = 0; serverEvents = []; modelBoxes.clear();
  actionFeedback.reset(); clearKeySecret();
  $('certificate-download').hidden = true;
  for (const id of ['output', 'events', 'job', 'hardware', 'installed', 'runtime-models', 'fit', 'catalog-progress', 'model-info-content', 'model-info-title', 'storage-current', 'storage-feedback', 'api-keys', 'users', 'notice']) $(id).replaceChildren();
  for (const node of document.querySelectorAll('#dashboard input, #dashboard textarea')) {
    if (node.type === 'checkbox') node.checked = node.defaultChecked;
    else node.value = node.defaultValue;
  }
  for (const id of ['families', 'tags', 'generate-model', 'api-key-models']) $(id).replaceChildren();
  draw([]);
}
/**
 * Encode a resident instance identity so identical names on different devices remain distinct.
 * @param {Object} model - Runtime model metadata with an optional target.
 * @returns {string} Stable selector/card identity.
 */
const modelKey = model => model.target ? JSON.stringify([model.name || model.model, model.target]) : (model.name || model.model);
/**
 * Decode a selector value into a model and optional hardware target.
 *
 * @param {string} value - Plain model name or JSON-encoded model/target pair.
 * @returns {{model: string, target?: string}} Selection sent to the API.
 */
function selectedModel(value) { if (value.startsWith('[')) { const [model, target] = JSON.parse(value); return {model, target}; } return {model: value}; }
/**
 * Return resident model instances only when the latest runtime state is healthy.
 *
 * @returns {Array<Object>} Loaded instances, including hardware target metadata.
 */
function loadedChoices() { return runtimeState && runtimeState.running && !runtimeState.error ? (runtimeState.loaded || []) : []; }
const modelBoxes = new Map();
const localActionEvents = [];
const actionFeedback = ActionFeedback.create({document, notice, log: logActionError});
/**
 * Merge local and persisted events, retaining scroll position and a 240-event limit.
 *
 * @param {Array<Object>} [events=[]] - Content-free server activity records.
 */
function renderEvents(events = []) {
  const panel = $('events'), top = panel.scrollTop, left = panel.scrollLeft;
  const merged = [...localActionEvents, ...events].sort((a, b) => b.timestamp - a.timestamp).slice(0, 240);
  panel.replaceChildren();
  for (const event of merged) panel.append(element('div', `${new Date(event.timestamp * 1000).toLocaleString()} · ${event.kind} · ${event.model} ${event.detail}`, 'event'));
  panel.scrollTop = top; panel.scrollLeft = left;
}
let serverEvents = [];
/**
 * Show an action failure locally and optionally persist bounded metadata; never retry logging failures.
 *
 * @param {{action: string, message: string}} failure - Safe control identity and error text.
 * @param {boolean} persist - False when the lifecycle worker already recorded the failure.
 */
function logActionError({action, message}, persist) {
  const event = {timestamp: Date.now() / 1000, kind: 'dashboard-action:failed', model: action, detail: message};
  localActionEvents.unshift(event); localActionEvents.splice(240); renderEvents(serverEvents);
  if (persist && csrf) {
    // Do not recursively report logging failures; retain the local event if offline.
    api('/api/action-errors', {action: action.slice(0, 300), message: message.slice(0, 2000)})
      .then(() => { event.persisted = true; }).catch(() => {});
  } else if (!persist) event.persisted = true;
}
/**
 * Submit lifecycle work and associate its returned job ID with the triggering control.
 *
 * @param {HTMLElement} control - Control receiving eventual job errors.
 * @param {string} path - Dashboard API endpoint.
 * @param {Object} body - Operation payload, not retained by action feedback.
 * @returns {Promise<Object>} Initial job snapshot.
 */
async function submitAction(control, path, body) {
  const result = await api(path, body);
  actionFeedback.track(control, result);
  return result;
}
/**
 * Reconcile running-model cards by model/target identity without rebuilding unchanged controls.
 * @param {boolean} [allowManagement=true] Include admin stop controls; switching boards clears cards.
 */
function renderAccess(allowManagement = true) {
  const active = loadedChoices();
  const identities = new Set(active.map(modelKey));
  for (const [identity, box] of modelBoxes) {
    if (!identities.has(identity)) { box.remove(); modelBoxes.delete(identity); }
  }
  for (const model of active) {
    const identity = modelKey(model), name = model.name || model.model;
    let box = modelBoxes.get(identity);
    if (!box) {
      box = element('section', '', 'card running-model');
      box.dataset.identity = identity;
      const head = element('div', '', 'section-head');
      head.append(element('h3', name));
      if (allowManagement) {
        const stop = element('button', '■ Stop model'); stop.type = 'button'; stop.dataset.stop = 'true';
        stop.setAttribute('aria-label', `Stop ${name} on ${model.hardware || model.target || 'CPU'}`);
        actionFeedback.bind(stop, JSON.stringify(['running-stop', name, model.target || 'cpu']));
        stop.addEventListener('click', safe(async (event, control) => { await submitAction(control, '/api/models/unload', {model: name, target: model.target || ''}); await refresh(); }));
        head.append(stop);
      }
      box.append(head);
      box.append(element('p', `Status: loaded · ${model.hardware || model.target || 'CPU'} · ${model.target || 'cpu'}`, 'model-status'));
      const details = element('details', '', 'access-menu');
      details.append(element('summary', 'Access model · API URL & Python examples'));
      details.append(element('p', 'Use a bearer key from Key Management. Set LLAMEROS_API_KEY and LLAMEROS_CA_CERT; never disable TLS verification.'));
      const fields = {};
      const feedback = element('p', ''); feedback.setAttribute('role', 'status');
      for (const [field, title] of [['url', 'Ollama API base URL'], ['openai', 'OpenAI-compatible base URL'], ['install', 'Install command'], ['code', 'Python example']]) {
        const label = element('label', title), input = element(field === 'code' ? 'textarea' : 'input', '');
        input.readOnly = true;
        if (field === 'code') { input.rows = 16; input.className = 'access-code'; input.spellcheck = false; }
        label.append(input); details.append(label); fields[field] = input;
        const copy = element('button', 'Copy ' + title); copy.type = 'button';
        actionFeedback.bind(copy, JSON.stringify(['copy', identity, field]));
        copy.addEventListener('click', async () => {
          actionFeedback.clear(copy);
          try { await navigator.clipboard.writeText(input.value); feedback.textContent = 'Copied to clipboard.'; }
          catch { input.focus(); input.select(); feedback.textContent = 'Text selected; press Ctrl+C or Command+C.'; actionFeedback.fail(copy, 'Clipboard unavailable. Text selected; press Ctrl+C or Command+C.'); }
        }); details.append(copy);
      }
      const label = element('label', 'Python client / framework'), framework = element('select', '');
      for (const [value, title] of Object.entries(ModelAccess.frameworks)) { const option = element('option', title); option.value = value; framework.append(option); }
      label.append(framework); details.insertBefore(label, details.children[2]);
      const update = () => {
        const url = accessConfig.secure_url;
        const example = ModelAccess.example(framework.value, name, url, accessContext, model.target || 'cpu');
        for (const [field, value] of Object.entries({url, openai: url + '/v1', install: example.install, code: example.code})) {
          if (fields[field].value !== value) fields[field].value = value;
        }
      };
      framework.addEventListener('change', update); box.updateAccess = update;
      details.append(feedback); box.append(details);
      modelBoxes.set(identity, box); $('runtime-models').append(box);
    }
    box.updateAccess();
  }
}
/**
 * Refresh the key-permission selector while retaining an existing explicit model selection.
 *
 * @param {Array<Object>} models - Loaded model metadata.
 */
function renderKeyModels(models) {
  const select = $('api-key-models'), previous = select.value || '*';
  const names = [...new Set(models.map(model => model.name || model.model))].sort();
  if (previous !== '*' && !names.includes(previous)) names.push(previous);
  const values = ['*', ...names];
  if (JSON.stringify([...select.options].map(option => option.value)) === JSON.stringify(values)) return;
  select.replaceChildren();
  for (const value of values) {
    const option = element('option', value === '*' ? 'All models' : value); option.value = value;
    select.append(option);
  }
  select.value = values.includes(previous) ? previous : (values[0] || '');
}
/**
 * Derive display status with deletion and revocation taking precedence over expiry.
 *
 * @param {Object} key - Safe key metadata; expiry is Unix seconds.
 * @returns {string} Human-readable lifecycle state.
 */
function keyState(key) {
  return key.deleted ? 'deleted' : key.revoked ? 'revoked' : key.expires && key.expires * 1000 <= Date.now() ? 'expired' : 'active';
}
/**
 * Discard the one-time plaintext key from the form and disable copying; never persist it.
 */
function clearKeySecret() {
  $('api-key-secret').value = ''; $('api-key-copy').disabled = true;
  $('api-key-result').hidden = true; $('api-key-feedback').textContent = '';
}
/**
 * Copy the masked one-time key, falling back to masked keyboard selection on clipboard denial.
 *
 * @returns {Promise<void>} Resolves after copy or visible fallback feedback.
 */
async function copyKeySecret() {
  actionFeedback.clear($('api-key-copy'));
  const field = $('api-key-secret');
  if (!field.value) return;
  try {
    await navigator.clipboard.writeText(field.value);
    $('api-key-feedback').textContent = 'Key copied. Store it securely; it cannot be retrieved later.';
  } catch {
    // Keep the secret masked even when Clipboard API access is denied.
    field.focus(); field.select();
    $('api-key-feedback').textContent = 'Clipboard unavailable. The masked key is selected; press Ctrl+C or Command+C.';
    actionFeedback.fail($('api-key-copy'), $('api-key-feedback').textContent);
  }
}
let modelUsageTable = null, keyUsageTable = null;
/**
 * Update the reusable key-usage table from server-scoped aggregates.
 *
 * @param {Array<Object>} keys - Safe metadata and usage, including deleted-key tombstones.
 */
function renderKeyUsage(keys) {
  if (!keyUsageTable) keyUsageTable = UsageTable.create($('key-usage'), UsageTable.keyColumns);
  keyUsageTable.update(keys.map(key => ({...key, status: keyState(key), limits_text: typeof TokenLimits === 'undefined' ? '' : TokenLimits.summary(key.limits), user_limits_text: typeof TokenLimits === 'undefined' ? '' : TokenLimits.summary(key.user_limits)})));
}
/**
 * Update overall model totals, shared by both boards rather than filtered by key owner.
 *
 * @param {Array<Object>} models - Per-model generation aggregates.
 */
function renderModelUsage(models) {
  if (!modelUsageTable) modelUsageTable = UsageTable.create($('usage'), UsageTable.modelColumns);
  modelUsageTable.update(models.map(model => ({...model, limits_text: typeof TokenLimits === 'undefined' ? '' : TokenLimits.summary(model.limits)})));
}
/**
 * Recompute lifecycle/playground button availability from known runtime state and active work.
 */
function updateControls() {
  const state = JobProgress.controls(runtimeState, busy);
  $('container-start').disabled = !state.start;
  $('container-stop').disabled = !state.stop;
  $('generate-model').disabled = busy || !state.active.length;
  $('generate-form').querySelector('button').disabled = busy || !loadedChoices().some(model => modelKey(model) === $('generate-model').value);
  $('storage-save').disabled = busy || !runtimeState || runtimeState.running || !!runtimeState.error;
  document.querySelectorAll('#installed button[data-action]').forEach(button => {
    const controls = JobProgress.modelControls(runtimeState, busy, button.dataset.model);
    button.disabled = !controls[button.dataset.action];
    button.title = button.dataset.action === 'load' && !controls.load
      ? (state.active.includes(button.dataset.model) ? 'This model is already running. Stop it before starting it again.' : 'Select a free hardware slot, or stop a running model first.') : '';
  });
  document.querySelectorAll('#runtime-models button[data-stop]').forEach(button => { button.disabled = busy; });
}
/**
 * Create a labeled progress indicator, supporting indeterminate values.
 *
 * @param {string} label - Accessible progress description.
 * @param {?number} value - Percentage, or null when unknown.
 */
function progressBar(label, value) {
  const wrapper = element('div', '', 'progress-row');
  wrapper.append(element('div', label));
  const bar = document.createElement('progress'); bar.max = 100; bar.setAttribute('aria-label', label);
  if (value !== null) bar.value = value;
  wrapper.append(bar); return wrapper;
}
/**
 * Paint lifecycle phase/layer progress and update the global busy state and action feedback.
 *
 * @param {Object} job - Current server job snapshot.
 */
function renderJob(job) {
  actionFeedback.job(job);
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
  updateControls();
  $('pull').disabled = busy || !selectedFit || !selectedFit.fits;
  if (busy) document.querySelectorAll('#installed button, #generate-form button, #storage-save').forEach(button => { button.disabled = true; });
}
/**
 * Poll lifecycle progress without overlapping requests and refresh the dashboard on completion.
 *
 * @returns {Promise<void>} Resolves after this poll, including guarded error handling.
 */
async function pollJob() {
  if (!csrf || pollingJob) return;
  pollingJob = true;
  const epoch = sessionEpoch;
  try { const job = await api('/api/job'); if (csrf) { const finished = busy && job.state !== 'running'; renderJob(job); if (finished) await refresh(); } } catch (error) { if (!error.staleSession) notice(error.message); }
  finally { if (epoch === sessionEpoch) pollingJob = false; }
}
/** @returns {boolean} Whether catalogue estimates are restricted to CPU RAM fit. */
const cpuOnly = () => $('cpu-only').checked;
/** @param {?number} n - Byte count. @returns {string} GiB label or an explicit unknown marker. */
const bytes = n => n == null ? 'unknown' : `${(n / 1073741824).toFixed(1)} GiB`;
/**
 * Replace the dashboard-wide status message using text content.
 *
 * @param {string} [message=""] - Message or empty string to clear.
 */
function notice(message = '') { if (message) $('notice').textContent = message; else $('notice').textContent = ''; }
/**
 * Call a session API, attach CSRF on writes, and clear local state on session expiry.
 * Personal-board key calls add board=user so admins also receive owner-scoped results.
 *
 * @param {string} path - Same-origin API path.
 * @param {Object} [body] - JSON payload; absence defaults to GET.
 * @param {string} [method] - Explicit HTTP method override.
 * @returns {Promise<Object>} Decoded JSON; rejects with a displayable API error.
 */
async function api(path, body, method) {
  const epoch = sessionEpoch;
  try {
  if (typeof userBoard !== 'undefined' && userBoard && path.startsWith('/api/keys')) path += (path.includes('?') ? '&' : '?') + 'board=user';
  const response = await fetch(path, {method: method || (body === undefined ? 'GET' : 'POST'),
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (epoch !== sessionEpoch) throw new Error('Stale session');
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') {
      const wasSignedIn = Boolean(csrf);
      signedOut();
      if (wasSignedIn) $('session-banner').hidden = false;
    }
    throw new Error(typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(item => item.msg || 'Invalid value').join('; ') : 'Request failed');
  }
  return data;
  } catch (error) {
    if (epoch !== sessionEpoch) { error.staleSession = true; error.message = ''; }
    throw error;
  }
}
/**
 * Capture the actual triggering button before asynchronous event dispatch clears currentTarget.
 *
 * @param {Event} event - Browser event, optionally a form submission.
 * @returns {?HTMLElement} Button/control to associate with action feedback.
 */
function actionControl(event) {
  if (!event || !event.currentTarget) return null;
  const node = event.currentTarget;
  return node.tagName === 'FORM' ? (event.submitter || node.querySelector('button[type=submit], button:not([type])')) : node;
}
/**
 * Wrap an async handler with preventDefault and control-local error feedback.
 *
 * @param {Function} fn - Handler receiving the event and captured control.
 * @returns {Function} Async event handler that displays failures instead of leaking rejections.
 */
function safe(fn) { return async event => {
  const epoch = sessionEpoch;
  if (event) event.preventDefault();
  // Capture before await: browsers clear currentTarget after event dispatch.
  const control = actionControl(event);
  actionFeedback.clear(control);
  try { await fn(event, control); }
  catch (error) { if (epoch === sessionEpoch && !error.staleSession) actionFeedback.fail(control, error.message || 'Operation failed'); }
}; }
/**
 * Build an element using textContent, never interpreting server strings as HTML.
 *
 * @param {string} tag - Element tag name.
 * @param {string} text - Plain text content.
 * @param {string} [className] - Optional CSS classes.
 * @returns {HTMLElement} Detached DOM element.
 */
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
/**
 * Replace a select with literal values and an optional empty placeholder.
 *
 * @param {HTMLSelectElement} select - Destination control.
 * @param {string[]} values - Labels and values.
 * @param {string} [placeholder] - Empty-choice label.
 */
function options(select, values, placeholder) {
  select.replaceChildren(); if (placeholder) select.add(new Option(placeholder, ''));
  values.forEach(value => select.add(new Option(value, value)));
}
/**
 * Enable model information only for a valid official catalogue selection.
 */
function updateModelInfo() {
  const link = $('model-info');
  const url = Catalogue.modelInfoUrl($('tags').value || $('families').value);
  link.disabled = !url;
}
/**
 * Hide the info overlay and invalidate pending responses, optionally restoring focus.
 *
 * @param {boolean} [restore=true] - Return focus to the model-info trigger.
 */
function closeModelInfo(restore = true) {
  infoVersion++; $('model-info-overlay').hidden = true;
  if (restore) $('model-info').focus();
}
$('model-info').addEventListener('click', safe(async (event, control) => {
  const model = $('tags').value || $('families').value, url = Catalogue.modelInfoUrl(model);
  if (!url) return;
  const version = ++infoVersion;
  $('model-info-overlay').hidden = false;
  $('model-info-title').textContent = model;
  $('model-info-content').textContent = 'Loading model description…';
  $('model-info-source').href = url;
  $('model-info-close').focus();
  try {
    const data = await api('/api/model-info?model=' + encodeURIComponent(model));
    if (version === infoVersion && csrf) $('model-info-content').textContent = data.description;
  } catch (error) { if (version === infoVersion) { $('model-info-content').textContent = 'Could not load model description: ' + error.message; throw error; } }
}));
$('model-info-close').addEventListener('click', () => closeModelInfo());
$('model-info-overlay').addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeModelInfo(); } });
$('generate-model').addEventListener('change', updateControls);
$('image-availability').addEventListener('click', safe(refresh));
/**
 * Clear session UI, timers, stale async versions, telemetry, and one-time secrets on logout.
 */
function signedOut() { clearSessionView(); if (typeof dashboardIdentity !== 'undefined') { dashboardIdentity = null; userBoard = false; $('board-switch').hidden = true; $('users').replaceChildren(); }  actionFeedback.reset(); localActionEvents.length = 0; serverEvents = [];  clearKeySecret(); $('api-key-add').open = false; $('api-keys').replaceChildren(); $('key-usage').replaceChildren(); if (keyUsageTable) keyUsageTable.update([]); if (modelUsageTable) modelUsageTable.update([]); closeModelInfo(false); runtimeState = null; accessConfig = null; renderAccess();  csrf = ''; catalogueVersion++; familyVersion++; fitVersion++; storageInitialized = false; $('config-menu').open = false; clearInterval(timer); clearInterval(jobTimer); jobTimer = null; timer = null; $('dashboard').hidden = true; $('login-panel').hidden = false; $('logout').hidden = true; $('identity').textContent = ''; }
/**
 * Initialize identity/board state, start polling, and refresh session-visible data.
 *
 * @param {Object} data - Session identity, CSRF token, and current role.
 * @returns {Promise<void>} Resolves when initial dashboard data has loaded.
 */
async function signedIn(data) {
  $('session-banner').hidden = true;
  clearSessionView();
  csrf = data.csrf; $('identity').textContent = data.username; $('dashboard').hidden = false;
  $('login-panel').hidden = true; $('logout').hidden = false; notice();
  if (data.role) configureBoard(data);
  clearInterval(timer); timer = setInterval(safe(refresh), 5000); clearInterval(jobTimer); jobTimer = setInterval(pollJob, 1000);
  if (data.role === 'user') { clearInterval(jobTimer); jobTimer = null; await Promise.all([refreshKeys(), refresh()]); return; }
  if (data.role === 'admin') await refreshUsers();
  // Load keys independently: unavailable runtimes/catalogue must not leave the table empty.
  await Promise.all([refreshKeys(), refresh(), catalogue()]);
}
for (const id of ['login-form', 'api-key-form', 'storage-form', 'generate-form', 'search-form']) {
  actionFeedback.bind($(id).querySelector('button[type=submit], button:not([type])'), id);
}
$('login-form').addEventListener('submit', safe(async () => {
  try { const data = await api('/api/login', {username: $('username').value, password: $('password').value}); await signedIn(data); }
  finally { $('password').value = ''; }
}));
$('logout').addEventListener('click', safe(async () => { const pending = api('/api/logout', {}); signedOut(); try { await pending; } catch { /* Local secrets stay cleared even when offline. */ } }));
$('refresh').addEventListener('click', safe(refresh));
/**
 * Fetch scoped keys and reconcile the management table without retaining any plaintext secret.
 *
 * @returns {Promise<void>} Resolves after authorized key metadata is rendered.
 */
async function refreshKeys() {
  const data = await api('/api/keys');
  if (!csrf) return;
  const scroll = $('api-key-table-scroll').scrollTop;
  $('api-keys').replaceChildren();
  for (const key of data.keys) {
    const row = element('tr', ''), state = keyState(key);
    const name = element('td', ''), models = element('td', ''), status = element('td', '');
    const nameText = element('span', key.name, 'key-cell-text'); nameText.title = key.name; name.append(nameText);
    if (key.user) name.append(element('small', key.user, 'key-cell-text'));
    const modelText = key.models.includes('*') ? 'All models' : key.models.join(', ');
    const modelLabel = element('span', modelText, 'key-cell-text'); modelLabel.title = modelText; models.append(modelLabel);
    const badge = element('span', state, 'key-status'); badge.dataset.state = state; status.append(badge);
    const expires = element('td', key.expires ? new Date(key.expires * 1000).toLocaleDateString() : 'Never');
    const actions = element('td', '', 'key-action'), button = element('button', '', 'key-delete'); button.type = 'button';
    actionFeedback.bind(button, 'delete-key:' + key.id);
    button.title = `Delete key ${key.name}`; button.setAttribute('aria-label', button.title);
    const icon = element('span', '🗑'); icon.setAttribute('aria-hidden', 'true'); button.append(icon);
    button.addEventListener('click', safe(async () => { await api('/api/keys/' + key.id, {}, 'DELETE'); clearKeySecret(); await refreshKeys(); await refresh(); }));
    if (typeof TokenLimits !== 'undefined') actions.append(TokenLimits.button('key', key.id, key.name));
    actions.append(button); row.append(name, models, status, expires, actions); $('api-keys').append(row);
  }
  if (!data.keys.length) {
    const row = element('tr', ''), cell = element('td', 'No keys yet. Open “Add key” below to securely share a model.');
    cell.colSpan = 5; cell.className = 'key-empty'; row.append(cell); $('api-keys').append(row);
  }
  $('api-key-table-scroll').scrollTop = scroll;
  actionFeedback.restore();
}
$('api-key-form').addEventListener('submit', safe(async () => {
  clearKeySecret();
  const owner = typeof dashboardIdentity !== 'undefined' && dashboardIdentity ? {user: userBoard ? dashboardIdentity.username : $('api-key-user').value.trim() || dashboardIdentity.username} : {};
  const result = await api('/api/keys', {...owner, ...(typeof TokenLimits !== 'undefined' ? {limits: TokenLimits.draft} : {}), name: $('api-key-name').value.trim(), models: [$('api-key-models').value || '*'], max_tokens: null, expires_days: $('api-key-days').value === 'never' ? null : Number($('api-key-days').value)});
  if (!csrf) return;
  $('api-key-secret').value = result.key; $('api-key-result').hidden = false; $('api-key-copy').disabled = false;
  if (typeof TokenLimits !== 'undefined') TokenLimits.reset();
  $('api-key-feedback').textContent = 'Key generated. Copy it now; only its digest is stored.';
  $('api-key-add').open = false; $('api-key-copy').focus();
  await refreshKeys(); await refresh();
}));
$('api-key-copy').addEventListener('click', copyKeySecret);
$('api-key-clear').addEventListener('click', clearKeySecret);
$('api-keys-refresh').addEventListener('click', safe(refreshKeys));
$('storage-form').addEventListener('submit', safe(async (event, control) => {
  await submitAction(control, '/api/storage', {directory: $('storage-directory').value.trim(), confirmed: $('storage-confirm').checked});
  $('storage-feedback').textContent = 'Storage change queued; see the runtime job for completion or errors.';
  $('storage-confirm').checked = false;
  await refresh();
}));
for (const action of ['start', 'stop']) $('container-' + action).addEventListener('click', safe(async (event, control) => { await submitAction(control, '/api/container/' + action, {}); await refresh(); }));
/**
 * Search families and progressively check compatibility with bounded request concurrency.
 * Version counters ignore stale responses after a new search, selection, or logout.
 *
 * @param {Event} [event] - Optional triggering event.
 * @param {HTMLElement} [control] - Control receiving partial-search error feedback.
 * @returns {Promise<void>} Resolves after this search and its workers finish.
 */
async function catalogue(event, control) {
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
  /**
   * Consume catalogue families until the shared queue ends or this search becomes stale.
   *
   * @returns {Promise<void>} Completion of one bounded-concurrency worker.
   */
  async function worker() {
    while (version === catalogueVersion && csrf && next < catalogueEntries.length) {
      const entry = catalogueEntries[next++];
      try {
        const result = await api('/api/catalog/' + encodeURIComponent(entry.name) + '/compatibility?cpu_only=' + cpu);
        if (version !== catalogueVersion) return;
        Object.assign(entry, {fits: result.fits, variants: result.variants});
      } catch (error) { if (version !== catalogueVersion) return; failures++; if (failures === 1) actionFeedback.fail(control, 'Some model compatibility checks failed: ' + error.message); }
      checked++; render();
    }
  }
  // Bound network concurrency; no registry request per tag or blocking full-catalogue scan.
  await Promise.all(Array.from({length: 3}, worker));
}
/**
 * Render estimated-fit options while preserving a still-valid selection.
 *
 * @param {HTMLSelectElement} select - Destination selector.
 * @param {Array<Object>} entries - Fit-annotated catalogue entries.
 * @param {string} placeholder - Empty-choice label.
 */
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
/**
 * Render currently visible catalogue families with fit-priority labels.
 */
function renderFamilies() {
  compatibleOptions($('families'), Catalogue.sort(Catalogue.visible(catalogueEntries, loadedCatalogueQuery, cpuOnly())), 'Select a family');
  updateModelInfo();
}
/**
 * Render at most six fitting recommendations and their selection actions.
 */
function renderRecommendations() {
  $('recommended').replaceChildren();
  for (const entry of Catalogue.recommendations(Catalogue.visible(catalogueEntries, loadedCatalogueQuery, cpuOnly()))) {
    const row = element('div', '', 'recommendation');
    const bullet = element('span', '●', 'fit-bullet'); bullet.setAttribute('aria-hidden', 'true');
    const button = element('button', entry.variant.name);
    actionFeedback.bind(button, 'recommendation:' + entry.variant.name);
    button.addEventListener('click', safe(async () => {
      renderFamilies(); $('families').value = entry.family;
      await selectFamily(entry.variant.name);
    }));
    row.append(bullet, button, element('small', `~${bytes(entry.variant.download_bytes)} · popularity rank ${entry.rank + 1}`));
    $('recommended').append(row);
  }
  actionFeedback.restore();
  if (!$('recommended').children.length) $('recommended').append(element('p', 'No compatible recommendations found yet. Unknown or oversized variants are not recommended.'));
}
$('search').addEventListener('input', () => {
  // Do not pretend a local substring filter is an upstream catalogue search.
  catalogueVersion++; familyVersion++; fitVersion++; selectedFit = null; $('pull').disabled = true;
  $('catalog-progress').textContent = 'Press Search / recheck to search Ollama for this query.';
});
$('cpu-only').addEventListener('change', safe(catalogue));
$('search-form').addEventListener('submit', safe(catalogue));
/**
 * Load a selected family's variants and ignore results superseded by newer selections.
 *
 * @param {string} [preferred=""] - Optional variant to select after loading.
 * @returns {Promise<void>} Resolves after tags and model-info availability are updated.
 */
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
/**
 * Check selected variant memory fit and suppress stale results before enabling download.
 *
 * @returns {Promise<void>} Resolves after the advisory fit is displayed.
 */
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
$('pull').addEventListener('click', safe(async (event, control) => {
  if (!selectedFit || !selectedFit.fits) return;
  await submitAction(control, '/api/models/pull', {model: selectedFit.model}); await refresh();
}));
$('generate-form').addEventListener('submit', safe(async (event, control) => {
  await submitAction(control, '/api/generate', {...selectedModel($('generate-model').value), prompt: $('prompt').value, max_tokens: 256});
  $('output').textContent = 'Generating…'; await refresh();
}));
/**
 * Refresh the active board without overlapping refreshes or painting after logout.
 * The user board uses its allowlisted endpoint instead of requesting admin-only data.
 *
 * @returns {Promise<void>} Resolves after the current dashboard snapshot is rendered.
 */
async function refresh() {
  if (refreshing || !csrf) return; refreshing = true;
  const epoch = sessionEpoch;
  try {
    const session = await api('/api/session');
    if (!csrf) return;
    if (dashboardIdentity && session.username !== dashboardIdentity.username) {
      signedOut(); await signedIn(session); return;
    }
    if (dashboardIdentity && session.role !== dashboardIdentity.role) {
      configureBoard(session); clearInterval(jobTimer); jobTimer = userBoard ? null : setInterval(pollJob, 1000);
      await refreshKeys();
    }
    if (userBoard) { await refreshUserBoard(); return; }
    const [data, history, inventory, keyUsage] = await Promise.all([api('/api/status'), api('/api/history'), api('/api/models'), api('/api/keys/usage')]);
    if (!csrf) return;
    renderKeyUsage(keyUsage.keys); renderKeyModels(inventory.models || []);
    const storage = inventory.storage;
    $('storage-current').textContent = `Active location: ${storage.location} (${storage.mode === 'directory' ? 'host directory' : 'Docker volume'})`;
    if (!storageInitialized) { $('storage-directory').value = storage.directory; $('storage-directory').placeholder = `Empty = Docker volume; example: ${storage.suggested_directory}`; storageInitialized = true; }
    runtimeState = data.ollama;
    accessConfig = {...data.access, secure_url: window.location.origin + '/inference'}; accessContext = data.context_length; renderAccess();
    const hw = data.hardware;
    $('hardware').replaceChildren();
    for (const [label, value, detail] of [
      ['CPU', hw.cpu_percent.toFixed(0) + '%', `${hw.logical_cores} threads · ${hw.cpu}`],
      ['RAM AVAILABLE', bytes(hw.memory_available), `${bytes(hw.memory_total)} total`],
      ['GPU', hw.gpus.length ? hw.gpus.map(g => g.vendor).join(', ') : 'CPU mode', hw.gpus.map(g => `${g.name}: ${bytes(g.free)} free`).join(' · ') || 'No supported GPU detected'],
      ['RUNTIME', data.ollama.running ? 'Online' : 'Offline', `${data.gpu_mode} · ${data.context_length} context`],
    ]) { const card = element('div', '', 'metric'); card.append(element('small', label), element('strong', value), element('small', detail)); $('hardware').append(card); }
    for (const slot of data.ollama.slots || []) {
      const card = element('div', '', 'metric');
      const names = (slot.loaded || []).map(model => model.name || model.model);
      card.append(element('small', slot.label), element('strong', slot.error ? 'Unavailable' : names.length ? `${names.length} model(s) loaded` : 'Free'), element('small', slot.error || names.join(', ') || 'No model assigned by Llameros'));
      if (slot.vendor !== 'cpu') card.append(element('small', `${bytes(slot.free)} available / ${bytes(slot.total)} ${slot.unified_memory ? 'unified memory (shared with CPU)' : 'VRAM'} · ${slot.utilization == null ? 'utilization unknown' : slot.utilization + '% utilization'}`));
      $('hardware').append(card);
    }
    const targetPrevious = $('load-target').value;
    const freeSlots = (data.ollama.slots || []).filter(slot => slot.available);
    options($('load-target'), freeSlots.map(slot => slot.id), freeSlots.length ? null : 'No free hardware');
    for (const option of $('load-target').options) { const slot = freeSlots.find(slot => slot.id === option.value); if (slot) option.textContent = slot.label; }
    if (freeSlots.some(slot => slot.id === targetPrevious)) $('load-target').value = targetPrevious;
    $('load-target').disabled = busy || !freeSlots.length;
    const runtimeStatus = JobProgress.runtimeState(data.ollama);
    $('runtime').textContent = runtimeStatus.text; $('runtime').dataset.state = runtimeStatus.state;
    const resource = data.ollama.resources;
    $('runtime-detail').textContent = data.ollama.error || data.ollama.legacy_error || (resource ? `Container CPU ${resource.CPUPerc} · memory ${resource.MemUsage} · network ${resource.NetIO}` : 'Start the managed container to download or run models.');
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
    const activeNames = loadedChoices().map(modelKey);
    const current = $('generate-model').value; options($('generate-model'), activeNames, activeNames.length ? null : 'Load a model first'); if (activeNames.includes(current)) $('generate-model').value = current;
    for (const option of $('generate-model').options) { const model = loadedChoices().find(item => modelKey(item) === option.value); if (model) option.textContent = `${model.name || model.model}${model.hardware ? ' · ' + model.hardware : ''}`; }
    $('installed').replaceChildren();
    if (!models.length) $('installed').append(element('p', ['unknown', 'unavailable'].includes(inventory.source) ? 'No inventory available. Start Ollama to check this storage location.' : 'No downloaded models found in the selected storage location.'));
    models.forEach(model => {
      const row = element('div', '', 'model'), active = loaded.filter(m => m.name === model.name);
      row.append(element('strong', model.name), element('small', `${bytes(model.size)}${active.length ? ' · loaded: ' + active.map(item => item.hardware || 'default runtime').join(', ') : ' · downloaded'}${model.modified_at ? ' · ' + new Date(model.modified_at).toLocaleDateString() : ''}`));
      const actions = element('div', '', 'actions');
      if (typeof TokenLimits !== 'undefined') actions.append(TokenLimits.button('model', model.name, model.name));
      for (const action of ['load', 'unload']) {
        const targets = action === 'unload' && active.length ? active : [{target: ''}];
        for (const target of targets) {
          const button = element('button', action === 'load' ? 'Load / start' : '■ Stop' + (target.hardware ? ' · ' + target.hardware : ''));
          button.dataset.action = action; button.dataset.model = model.name;
          actionFeedback.bind(button, JSON.stringify(['model', action, model.name, target.target || '']));
          button.addEventListener('click', safe(async (event, control) => { await submitAction(control, '/api/models/' + action, {model: model.name, target: action === 'load' ? $('load-target').value : target.target || ''}); await refresh(); })); actions.append(button);
        }
      }
      row.append(actions); $('installed').append(row);
    });
    renderModelUsage(history.models);
    serverEvents = history.events;
    for (let index = localActionEvents.length - 1; index >= 0; index--) {
      const local = localActionEvents[index];
      if (local.persisted && serverEvents.some(event => event.detail === local.detail &&
          (event.model === local.model || event.kind.endsWith(':failed')) &&
          Math.abs(event.timestamp - local.timestamp) < 10)) localActionEvents.splice(index, 1);
    }
    renderEvents(serverEvents);
    actionFeedback.restore();
    updateControls();
    draw(history.samples);
  } finally { if (epoch === sessionEpoch) refreshing = false; }
}
/**
 * Draw CPU/RAM and individual GPU histories; unavailable metrics leave gaps.
 *
 * @param {Array<Object>} samples - Timestamped host samples; missing values are tolerated.
 */
function draw(samples) {
  const canvas = $('chart'), ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  const gpuId = (gpu, index) => String(gpu.id ?? `index-${index}`);
  const devices = new Map();
  for (const sample of samples) (sample.hardware?.gpus || []).forEach((gpu, index) => {
    const id = gpuId(gpu, index);
    if (!devices.has(id)) devices.set(id, `GPU ${devices.size + 1}${gpu.name ? ' · ' + gpu.name : ''}`);
  });
  const series = [['cpu_percent', '#bcf48c', 'CPU'], ['memory_percent', '#ffbc86', 'RAM'],
    ...[...devices].map(([id, label], index) => [id, index === 0 ? '#83bfff' : `hsl(${(210 + index * 137.5) % 360}, 75%, 70%)`, label])];
  const legend = $('chart-legend');
  legend.replaceChildren();
  for (const [, color, label] of series) {
    const item = element('span', '● ' + label); item.style.color = color; legend.append(item);
  }
  ctx.clearRect(0, 0, w, h); ctx.strokeStyle = '#303e35'; ctx.fillStyle = '#a1b2a6'; ctx.font = '12px system-ui';
  for (const pct of [0, 50, 100]) { const y = 15 + (100 - pct) / 100 * (h - 35); ctx.beginPath(); ctx.moveTo(40, y); ctx.lineTo(w, y); ctx.stroke(); ctx.fillText(pct + '%', 0, y + 4); }
  if (!samples.length) { ctx.fillText('Resource history appears after the first sample.', 60, 90); return; }
  for (const [seriesIndex, [key, color]] of series.entries()) {
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
    let connected = false;
    samples.forEach((sample, i) => {
      const hardware = sample.hardware || {};
      const value = seriesIndex < 2 ? hardware[key]
        : (hardware.gpus || []).find((gpu, index) => gpuId(gpu, index) === key)?.utilization;
      if (!Number.isFinite(value) || value < 0 || value > 100) { connected = false; return; }
      const x = 42 + i / Math.max(1, samples.length - 1) * (w - 45), y = 15 + (100 - value) / 100 * (h - 35);
      if (connected) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      connected = true;
    }); ctx.stroke();
  }
}
safe(async () => { try { const data = await api('/api/session'); await signedIn(data); } catch (error) { if (csrf) notice(error.message); else signedOut(); } })();
