/** Opt-in community exchange. Upload only after displaying and confirming the exact payload. */
'use strict';
const BenchmarkSharing = {
  data: null,
  reset() { this.data = null; document.getElementById('sharing-voucher').value = ''; document.getElementById('community-models').replaceChildren(); document.getElementById('sharing-preview').textContent = ''; document.getElementById('sharing-feedback').textContent = ''; },
  render(data) {
    if (this.data) return;
    this.data = data;
    document.getElementById('sharing-enabled').checked = data.enabled;
    document.getElementById('sharing-host').value = data.host;
    document.getElementById('sharing-port').value = data.port;
    document.getElementById('sharing-ca').value = data.ca_certificate;
  },
  async recommendations(cpuOnly) {
    const panel = document.getElementById('community-models');
    const epoch = sessionEpoch;
    const data = await api('/api/benchmark-sharing/recommendations?cpu_only=' + cpuOnly);
    if (epoch !== sessionEpoch || userBoard) return;
    panel.replaceChildren(element('p', data.note));
    for (const kind of ['chat', 'embedding']) {
      for (const timing of ['generation', 'request', 'mixed']) {
        const rows = data.models.filter(row => row.kind === kind && row.timing === timing).slice(0, 6);
        if (!rows.length) continue;
        panel.append(element('h4', (kind === 'chat' ? 'Chat · output tokens/s' : 'Embedding · input tokens/s') + ' · ' + timing + ' timing'));
        for (const row of rows) panel.append(element('p', `${row.model} · ${row.tokens_per_second.toFixed(1)} tokens/s · range ${row.minimum}–${row.maximum} · ${row.samples} distinct approved measurements (not independent users) · ${row.hardware.accelerator} · artifact ${row.model_digest || 'unknown'}. Search this tag and check local fit before downloading.`));
      }
    }
  },
};
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('sharing-form').addEventListener('submit', safe(async event => {
    event.preventDefault();
    const data = await api('/api/benchmark-sharing', {
      enabled: document.getElementById('sharing-enabled').checked,
      host: document.getElementById('sharing-host').value,
      port: Number(document.getElementById('sharing-port').value),
      ca_certificate: document.getElementById('sharing-ca').value.trim(),
    });
    BenchmarkSharing.reset(); BenchmarkSharing.render(data);
    document.getElementById('sharing-feedback').textContent = 'Saved. No benchmarks were uploaded. Sharing always requires manual confirmation.';
  }));
  document.getElementById('share-benchmarks').addEventListener('click', safe(async () => {
    const voucher = document.getElementById('sharing-voucher').value.trim();
    if (!/^[A-Za-z0-9_-]{43}$/.test(voucher)) { document.getElementById('sharing-feedback').textContent = 'Enter a valid single-use voucher from the statistics server operator (up to 10 entries).'; return; }
    const epoch = sessionEpoch;
    const preview = await api('/api/benchmark-sharing/preview');
    if (epoch !== sessionEpoch || userBoard) return;
    const payload = JSON.stringify({entries: preview.entries}, null, 2);
    document.getElementById('sharing-preview').textContent = payload;
    if (!preview.entries.length) { document.getElementById('sharing-feedback').textContent = 'No eligible results. Run a new benchmark of an official public-library model first.'; return; }
    if (!window.confirm(`Submit these benchmarks for review at ${preview.destination}?\n\n${payload}\n\nThese public fields plus your secret single-use upload voucher are sent over HTTPS. The voucher is not included in public records. No usernames, hostnames, UUIDs, prompts, account keys, timestamps or instance IDs. The server/network operator can see the connection IP; hardware combinations can be distinctive. After approval, results become public and cannot be individually retracted through this UI.\n\n${preview.note}`)) return;
    document.getElementById('sharing-voucher').value = '';
    const result = await api('/api/benchmark-sharing/share', {entries: preview.entries, confirmed: true, destination: preview.destination, voucher});
    if (epoch !== sessionEpoch || userBoard) return;
    document.getElementById('sharing-feedback').textContent = `Submitted ${result.accepted} results for operator review; ${result.duplicates} duplicates ignored. Nothing affects recommendations until approved. Voucher consumed.`;
  }));
});