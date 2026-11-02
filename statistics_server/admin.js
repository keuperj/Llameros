/** Owner-only administration. Render untrusted benchmark fields exclusively as text. */
'use strict';
const $ = id => document.getElementById(id);
let csrf = '', offset = 0, total = 0, epoch = 0, loading = false, refreshQueued = false;
const pageSize = 25;
function node(tag, text = '') { const item = document.createElement(tag); item.textContent = text; return item; }
function signedOut() {
  epoch++; csrf = ''; offset = 0; total = 0;
  $('password').value = ''; $('username').value = ''; $('dashboard').hidden = true; $('login-form').hidden = false;
  $('entries').replaceChildren(); $('overview').textContent = ''; $('page-info').textContent = '';
}
async function api(path, payload) {
  const response = await fetch('/admin/api/' + path, {
    method: payload === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    headers: payload === undefined ? {} : {'Content-Type':'application/json', 'X-CSRF-Token':csrf},
    body: payload === undefined ? undefined : JSON.stringify(payload),
    referrerPolicy: 'no-referrer',
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) signedOut();
    // Never display server-supplied validation inputs, passwords or secrets.
    throw new Error(response.status === 401 ? 'Sign-in required or authentication failed.' :
      response.status === 429 ? 'Too many requests; retry in a minute.' :
      response.status === 409 ? 'Entry changed or lacks valid evidence. Refresh and review again.' : 'Request failed. Check your session and input, then retry.');
  }
  return data;
}
const safe = fn => async event => { try { await fn(event); } catch (error) { $('feedback').textContent = error.message; } };
function renderRows(rows) {
  $('entries').replaceChildren();
  for (const row of rows) {
    const item = row.entry, tr = node('tr');
    if (row.flags.includes('disagrees-with-approved-results')) tr.className = 'result-disagreement';
    tr.append(node('td', `${item.model || 'Legacy entry'} · ${item.model_digest || 'unknown artifact'} · ${row.status}`));
    const hw = item.hardware || {};
    tr.append(node('td', `${hw.architecture || 'unknown'} · ${hw.logical_cores ?? '?'} cores · ${hw.memory_gib ?? '?'} GiB RAM · ${hw.accelerator || 'unknown'} · ${hw.accelerator_memory_gib ?? '?'} GiB VRAM`));
    tr.append(node('td', `${item.tokens_per_second ?? '?'} tokens/s · ${item.kind || 'unknown'} · ${item.timing || 'unknown'} · context ${item.context_length ?? '?'} · parallel ${item.parallel ?? '?'} · Flash Attention ${item.flash_attention ?? '?'} · cache ${item.kv_cache_type || '?'}`));
    tr.append(node('td', row.flags.length ? row.flags.join(', ') : 'No automatic warnings (not proof)'));
    const evidence = node('td'), details = node('details');
    details.append(node('summary', 'Six-case evidence / full payload'), node('pre', JSON.stringify(item, null, 2)), node('p', `Payload digest: ${row.digest}`));
    evidence.append(details); tr.append(evidence);
    const actions = node('td');
    for (const [status, label] of [['approved', 'Approve / publish'], ['rejected', 'Reject / unpublish']]) {
      if (status === row.status) continue;
      const button = node('button', label); button.type = 'button';
      button.addEventListener('click', safe(async () => {
        if (!window.confirm(`${label} ${item.model || 'this entry'}?\nDigest: ${row.digest}\n${status === 'approved' ? 'This includes the result in the public average and recommendations. Approval is not proof of genuine hardware.' : 'This removes the result from public browsing and future recommendation downloads.'}`)) return;
        button.disabled = true;
        try {
          await api('benchmarks/' + encodeURIComponent(row.digest), {status, expected_status:row.status, confirmed:true});
          $('feedback').textContent = 'Moderation decision saved.';
          await refresh();
        } finally { button.disabled = false; }
      }));
      actions.append(button);
    }
    tr.append(actions); $('entries').append(tr);
  }
}
async function refresh() {
  if (!csrf) return;
  if (loading) { refreshQueued = true; return; }
  loading = true;
  const version = epoch, status = $('status').value, currentOffset = offset;
  try {
    const [summary, page] = await Promise.all([api('overview'), api(`benchmarks?status=${encodeURIComponent(status)}&offset=${currentOffset}&limit=${pageSize}`)]);
    if (version !== epoch || !csrf || status !== $('status').value || currentOffset !== offset) return;
    $('overview').textContent = `${summary.entries.pending} pending · ${summary.entries.approved} approved · ${summary.entries.rejected} rejected · capacity ${summary.used}/${summary.capacity}. No independent-user counts.`;
    total = page.total;
    if (offset && offset >= total) { offset = Math.max(0, Math.floor((total - 1) / pageSize) * pageSize); }
    else renderRows(page.entries);
    $('page-info').textContent = `${status}: ${total} entries · page ${Math.floor(offset / pageSize) + 1}`;
    $('previous').disabled = offset === 0; $('next').disabled = offset + pageSize >= total;
  } finally {
    loading = false;
    if (refreshQueued) { refreshQueued = false; if (csrf) await refresh(); }
  }
  if (currentOffset !== offset && csrf) await refresh();
}
$('login-form').addEventListener('submit', safe(async event => {
  event.preventDefault(); const username = $('username').value, password = $('password').value;
  $('password').value = '';
  const data = await api('login', {username, password});
  epoch++; csrf = data.csrf; $('username').value = '';
  $('login-form').hidden = true; $('dashboard').hidden = false; $('feedback').textContent = 'Signed in.';
  await refresh();
}));
$('logout').addEventListener('click', safe(async () => {
  try { await api('logout', {}); } finally { signedOut(); }
}));
$('refresh').addEventListener('click', safe(refresh));
$('status').addEventListener('change', safe(async () => { offset = 0; await refresh(); }));
$('previous').addEventListener('click', safe(async () => { offset = Math.max(0, offset - pageSize); await refresh(); }));
$('next').addEventListener('click', safe(async () => { if (offset + pageSize < total) offset += pageSize; await refresh(); }));
window.addEventListener('pagehide', signedOut);
async function restore() {
  try {
    const data = await api('session'); csrf = data.csrf;
    $('login-form').hidden = true; $('dashboard').hidden = false; await refresh();
  } catch { signedOut(); }
}
restore();
setInterval(() => { if (csrf) safe(refresh)(); }, 30000);