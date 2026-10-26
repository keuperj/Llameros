/**
 * @file Llameros admin/user board presentation and account-policy controls.
 * Switching an admin to the personal board never changes the authenticated server role.
 */
'use strict';
let userBoard = false, dashboardIdentity = null;

/**
 * Switch dashboard presentation and clear scoped data/secrets without changing the server role.
 *
 * @param {Object} data - Current session identity and actual role.
 * @param {boolean} [asUser] - Defaults to true for ordinary users; admins may preview their board.
 */
function configureBoard(data, asUser = data.role !== 'admin') {
  if (typeof clearSessionView === 'function') clearSessionView();
  if (typeof TokenLimits !== 'undefined') TokenLimits.reset();
  dashboardIdentity = data; userBoard = asUser;
  document.getElementById('certificate-download').hidden = data.certificate_download_enabled !== true;
  if (data.role === 'user') document.getElementById('api-key-days').value = '7';
  const switcher = document.getElementById('board-switch');
  switcher.hidden = data.role !== 'admin';
  switcher.textContent = asUser ? 'Switch to admin board' : 'Switch to user board';
  document.getElementById('identity').textContent = `${data.username} · ${data.role} · ${asUser ? 'user' : 'admin'} board`;
  for (const node of document.querySelectorAll('#dashboard > *')) {
    node.hidden = asUser && !['runtime-models', 'key-management', 'usage-history', 'user-intro'].includes(node.id);
  }
  document.getElementById('user-intro').hidden = !asUser;
  for (const id of ['events', 'events-heading']) document.getElementById(id).hidden = asUser;
  document.getElementById('usage-description').textContent = asUser
    ? 'Host CPU and RAM history and overall usage by model across all users. Usage by key shows only your own keys, including deleted keys. Requests sent directly to Ollama are not included in token totals.'
    : 'Host CPU and RAM samples, plus per-model inference totals. Requests sent directly to Ollama are not included in token totals.';
  document.getElementById('api-key-owner-label').hidden = asUser;
  clearKeySecret();
  document.getElementById('api-key-user').value = data.username;
  document.getElementById('runtime-models').replaceChildren(); modelBoxes.clear();
  renderKeyUsage([]); renderModelUsage([]);
  document.getElementById('api-keys').replaceChildren();
  document.getElementById('users').replaceChildren();
}

/**
 * Render allowlisted resident models, overall model totals, and owner-only key statistics.
 *
 * @returns {Promise<void>} Ignores results after logout or a switch back to the admin board.
 */
async function refreshUserBoard() {
  const data = await api('/api/user/dashboard');
  if (!csrf || !userBoard) return;
  runtimeState = {running: true, loaded: data.models};
  accessConfig = {secure_url: window.location.origin + '/inference'};
  accessContext = data.context_length;
  const panel = document.getElementById('runtime-models');
  if (!modelBoxes.size) panel.replaceChildren();
  renderAccess(false);
  if (!data.models.length) panel.replaceChildren(element('p', 'No models are currently running. An administrator can start a model.'));
  renderKeyModels(data.models); renderKeyUsage(data.keys); renderModelUsage(data.history.models); draw(data.history.samples);
}

/**
 * Render administrator account controls while preserving scroll and disabling self-mutations.
 *
 * @returns {Promise<void>} Resolves after account rows and policy-save handlers are installed.
 */
async function refreshUsers() {
  const data = await api('/api/users');
  if (!csrf || userBoard) return;
  const panel = document.getElementById('users'); panel.replaceChildren();
  const scroll = document.getElementById('users-scroll'), top = scroll.scrollTop, left = scroll.scrollLeft;
  for (const user of data.users) {
    const row = element('tr', '');
    const name = element('th', user.username); name.scope = 'row'; row.append(name);
    const role = document.createElement('select'); role.setAttribute('aria-label', `Role for ${user.username}`);
    for (const value of ['user', 'admin']) role.add(new Option(value, value)); role.value = user.role;
    const blocked = document.createElement('input'); blocked.type = 'checkbox'; blocked.checked = Boolean(user.blocked);
    blocked.setAttribute('aria-label', `Block ${user.username}`);
    const save = element('button', 'Save'); save.type = 'button';
    save.setAttribute('aria-label', `Save access for ${user.username}`);
    if (user.username === dashboardIdentity.username) { role.disabled = true; blocked.disabled = true; save.disabled = true; }
    const models = document.createElement('input'); models.value = (user.models || []).join(', '); models.setAttribute('aria-label', `Allowed models for ${user.username}`);
    const targets = document.createElement('input'); targets.value = (user.targets || []).join(', '); targets.setAttribute('aria-label', `Allowed targets for ${user.username}`);
    const days = document.createElement('input'); days.type = 'number'; days.min = '1'; days.max = '90'; days.value = ''; days.placeholder = 'Renew 1–90 days'; days.setAttribute('aria-label', `Renew grant for ${user.username}`);
    for (const control of [role, blocked, save, models, targets, days]) { const cell = element('td', ''); cell.append(control); row.append(cell); }
    if (typeof TokenLimits !== 'undefined') {
      const cell = element('td', TokenLimits.summary(user.limits));
      cell.append(TokenLimits.button('user', user.username, user.username)); row.append(cell);
    }
    save.addEventListener('click', safe(async () => {
      await api('/api/users/' + encodeURIComponent(user.username), {role: role.value, blocked: blocked.checked, models: models.value.split(',').map(v => v.trim()).filter(Boolean), targets: targets.value.split(',').map(v => v.trim()).filter(Boolean), ...(days.value ? {grant_days: Number(days.value)} : {})});
      notice(`Updated ${user.username}. Blocking revokes all keys; unblocking does not restore them.`);
      await refreshUsers(); await refreshKeys();
    })); panel.append(row);
  }
  scroll.scrollTop = top; scroll.scrollLeft = left;
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('board-switch').addEventListener('click', safe(async () => {
    const data = await api('/api/session');
    configureBoard(data, !userBoard);
    clearInterval(jobTimer); jobTimer = userBoard ? null : setInterval(pollJob, 1000);
    await refreshKeys(); await refresh();
    if (!userBoard) await Promise.all([refreshUsers(), catalogue()]);
  }));
  document.getElementById('users-refresh').addEventListener('click', safe(refreshUsers));
  document.getElementById('user-refresh').addEventListener('click', safe(async () => { await refreshKeys(); await refresh(); }));
});