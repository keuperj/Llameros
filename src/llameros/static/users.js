'use strict';
let userBoard = false, dashboardIdentity = null;

function configureBoard(data, asUser = data.role !== 'admin') {
  dashboardIdentity = data; userBoard = asUser;
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
    ? 'Host CPU and RAM history, plus inference totals for your own keys only. Deleted keys remain in history. Tokens/s is unavailable for API usage.'
    : 'Host CPU and RAM samples, plus per-model inference totals. Requests sent directly to Ollama are not included in token totals.';
  document.getElementById('api-key-owner-label').hidden = asUser;
  clearKeySecret();
  document.getElementById('api-key-user').value = data.username;
  document.getElementById('runtime-models').replaceChildren(); modelBoxes.clear();
  renderKeyUsage([]); renderModelUsage([]);
  document.getElementById('api-keys').replaceChildren();
  document.getElementById('users').replaceChildren();
}

async function refreshUserBoard() {
  const data = await api('/api/user/dashboard');
  if (!csrf || !userBoard) return;
  const panel = document.getElementById('runtime-models'); panel.replaceChildren();
  for (const model of data.models) {
    const card = element('section', '', 'card running-model');
    card.append(element('h3', model.name || model.model), element('p', `Loaded · ${model.hardware || model.target || 'CPU'}`));
    const url = element('input', ''); url.readOnly = true; url.value = window.location.origin + '/inference';
    const label = element('label', 'Authenticated API base URL'); label.append(url); card.append(label);
    panel.append(card);
  }
  if (!data.models.length) panel.append(element('p', 'No models are currently running. An administrator can start a model.'));
  renderKeyModels(data.models); renderKeyUsage(data.keys); renderModelUsage(data.history.models); draw(data.history.samples);
}

async function refreshUsers() {
  const data = await api('/api/users');
  if (!csrf || userBoard) return;
  const panel = document.getElementById('users'); panel.replaceChildren();
  for (const user of data.users) {
    const row = element('form', '', 'inline');
    row.append(element('strong', user.username));
    const role = document.createElement('select'); role.setAttribute('aria-label', `Role for ${user.username}`);
    for (const value of ['user', 'admin']) role.add(new Option(value, value)); role.value = user.role;
    const blocked = document.createElement('input'); blocked.type = 'checkbox'; blocked.checked = Boolean(user.blocked);
    const label = element('label', 'Blocked'); label.append(blocked);
    const save = element('button', 'Save'); save.type = 'submit';
    if (user.username === dashboardIdentity.username) { role.disabled = true; blocked.disabled = true; save.disabled = true; }
    row.append(role, label, save);
    row.addEventListener('submit', safe(async () => {
      await api('/api/users/' + encodeURIComponent(user.username), {role: role.value, blocked: blocked.checked});
      notice(`Updated ${user.username}. Blocking revokes all keys; unblocking does not restore them.`);
      await refreshUsers(); await refreshKeys();
    })); panel.append(row);
  }
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