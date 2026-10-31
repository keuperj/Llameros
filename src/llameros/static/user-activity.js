/** Hourly token totals, including empty hours, without request content. */
'use strict';
const UserActivity = {
  data: null,
  aggregate(data, user = '') {
    const hours = new Map();
    for (let hour = data.start; hour <= data.end; hour += 3600) hours.set(hour, {hour, input: 0, output: 0, requests: 0, failed: 0});
    for (const row of data.rows) {
      if (user && row.username !== user) continue;
      const bucket = hours.get(row.hour);
      if (bucket) { bucket.input += row.input_tokens; bucket.output += row.output_tokens; bucket.requests += row.requests || 0; bucket.failed += row.failed || 0; }
    }
    return [...hours.values()];
  },
  reset() {
    this.data = null;
    document.getElementById('token-user')?.replaceChildren();
    const canvas = document.getElementById('token-chart');
    canvas?.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    const requests = document.getElementById('request-chart');
    requests?.getContext('2d').clearRect(0, 0, requests.width, requests.height);
    const totals = document.getElementById('token-totals'); if (totals) totals.textContent = '';
  },
  render(data) {
    this.data = data;
    const select = document.getElementById('token-user'), previous = select.value;
    select.replaceChildren(new Option('All users', ''));
    for (const user of data.users) select.add(new Option(user || 'Unknown', user));
    if (data.users.includes(previous)) select.value = previous;
    this.draw();
  },
  draw() {
    if (!this.data) return;
    const rows = this.aggregate(this.data, document.getElementById('token-user').value);
    this.plot('token-chart', rows, [['input', '#9cdd9b'], ['output', '#e7ad68']]);
    this.plot('request-chart', rows, [['requests', '#7dcfff'], ['failed', '#ff5959']]);
    const totals = field => rows.reduce((sum, row) => sum + row[field], 0).toLocaleString();
    document.getElementById('token-totals').textContent = `Input: ${totals('input')} · Output: ${totals('output')} tokens · Requests: ${totals('requests')} · Failed: ${totals('failed')} · hourly UTC · retained 90 days`;
  },
  plot(id, rows, series) {
    const canvas = document.getElementById(id), ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height, left = 70, right = w - 20, bottom = h - 36, top = 24;
    ctx.clearRect(0, 0, w, h);
    const max = Math.max(1, ...rows.flatMap(row => series.map(([field]) => row[field])));
    ctx.font = '12px sans-serif'; ctx.fillStyle = '#b7c9bb';
    for (let tick = 0; tick <= 4; tick++) {
      const y = bottom - (bottom - top) * tick / 4;
      ctx.fillText(Math.round(max * tick / 4).toLocaleString(), 2, y + 4);
      ctx.strokeStyle = '#344039'; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    }
    for (const [field, color] of series) {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
      rows.forEach((row, index) => {
        const x = left + index / Math.max(1, rows.length - 1) * (right - left);
        const y = bottom - row[field] / max * (bottom - top);
        if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }); ctx.stroke();
    }
    ctx.fillStyle = '#b7c9bb';
    for (let tick = 0; tick <= 4; tick++) {
      const index = Math.round((rows.length - 1) * tick / 4);
      const label = new Date(rows[index].hour * 1000).toISOString().slice(0, 13).replace('T', ' ');
      ctx.fillText(label, left + (right - left) * tick / 4 - (tick === 4 ? 110 : 0), h - 10);
    }
  },
};
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('token-user').addEventListener('change', () => UserActivity.draw());
  const tabs = [...document.querySelectorAll('[data-usage-tab]')];
  function activate(tab) {
    for (const button of tabs) {
      const selected = button === tab;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute('aria-controls')).hidden = !selected;
    }
    UserActivity.draw();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      activate(tabs[next]); tabs[next].focus();
    });
  });
});