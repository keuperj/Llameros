/** Admin-only retained request and lifecycle activity, with durable acknowledgement. */
'use strict';
const ActivityLog = {
  data: null, userErrors: false, adminErrors: false,
  failed(row) { return row.kind ? row.kind.endsWith(':failed') : row.status !== 'completed'; },
  reset() {
    this.data = null; this.userErrors = false; this.adminErrors = false;
    for (const id of ['activity-users', 'activity-summary']) document.getElementById(id)?.replaceChildren();
    const warning = document.getElementById('activity-warning'); if (warning) warning.hidden = true;
  },
  table(body, rows, columns) {
    const scroll = body.closest('.activity-scroll'), top = scroll.scrollTop, left = scroll.scrollLeft;
    body.replaceChildren();
    for (const row of rows) {
      const tr = element('tr', '', this.failed(row) ? 'activity-error' : '');
      for (const column of columns) tr.append(element('td', String(column(row) ?? '—')));
      body.append(tr);
    }
    scroll.scrollTop = top; scroll.scrollLeft = left;
  },
  render(data) {
    this.data = data;
    const count = data.alert.failures - data.alert.acknowledged;
    const warning = document.getElementById('activity-warning'); warning.hidden = count <= 0;
    document.getElementById('activity-warning-text').textContent = `${count} failed requests have not been acknowledged.`;
    const summary = document.getElementById('activity-summary'); summary.replaceChildren();
    for (const [label, counts] of [['User/API requests', data.request_counts], ['Admin activity events', data.admin_counts]]) {
      const row = element('tr', '');
      for (const value of [label, counts.total, counts.successful, counts.failed]) row.append(element('td', String(value)));
      summary.append(row);
    }
    const date = row => new Date(row.timestamp * 1000).toLocaleString();
    this.table(document.getElementById('activity-users'), data.requests.filter(row => !this.userErrors || this.failed(row)),
      [date, r => r.username || 'Unknown', r => r.key_name ? `${r.key_name} (${r.key_id})` : 'Unknown',
        r => r.model || 'Unknown', r => r.target || '—', r => r.status, r => r.prompt_tokens,
        r => r.output_tokens, r => r.tokens_per_second == null ? '—' : r.tokens_per_second.toFixed(1)]);
    document.getElementById('activity-user-filter').textContent = this.userErrors ? 'Show all requests' : 'Show only errors';
    document.getElementById('activity-user-filter').setAttribute('aria-pressed', String(this.userErrors));
  },
  events(rows) {
    this.table(document.getElementById('events'), rows.filter(row => !this.adminErrors || this.failed(row)),
      [r => new Date(r.timestamp * 1000).toLocaleString(), r => r.kind, r => r.model || '—', r => r.detail || '—']);
    document.getElementById('activity-admin-filter').textContent = this.adminErrors ? 'Show all activities' : 'Show only errors';
    document.getElementById('activity-admin-filter').setAttribute('aria-pressed', String(this.adminErrors));
  },
};
document.addEventListener('DOMContentLoaded', () => {
  const tabs = [...document.querySelectorAll('[data-activity-tab]')];
  function activate(tab) {
    for (const button of tabs) {
      const selected = button === tab;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute('aria-controls')).hidden = !selected;
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      activate(tabs[next]); tabs[next].focus();
    });
  }
  document.getElementById('activity-user-filter').addEventListener('click', () => {
    ActivityLog.userErrors = !ActivityLog.userErrors; if (ActivityLog.data) ActivityLog.render(ActivityLog.data);
  });
  document.getElementById('activity-admin-filter').addEventListener('click', () => {
    ActivityLog.adminErrors = !ActivityLog.adminErrors; renderEvents(serverEvents);
  });
  document.getElementById('activity-acknowledge').addEventListener('click', safe(async () => {
    if (!ActivityLog.data) return;
    await api('/api/activity/acknowledge', {failures: ActivityLog.data.alert.failures}); await refresh();
  }));
});