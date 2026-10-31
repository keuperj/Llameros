/** Persisted benchmark rankings; never mix embedding and generation throughput. */
'use strict';
const BenchmarkResults = {
  ranked(results, kind) {
    return results.filter(row => row.kind === kind && row.status === 'completed' && Number.isFinite(row.tokens_per_second))
      .sort((a, b) => b.tokens_per_second - a.tokens_per_second);
  },
  render(results = []) {
    const panel = document.getElementById('benchmark-results');
    if (!panel) return;
    const expanded = new Set([...panel.querySelectorAll('details[open]')].map(node => node.dataset.identity));
    panel.replaceChildren();
    if (!results.length) { panel.append(element('p', 'No benchmarks yet. Administrators can benchmark a running model.')); return; }
    for (const [kind, label] of [['chat', 'Chat · output tokens/s'], ['embedding', 'Embedding · input tokens/s']]) {
      const ranked = this.ranked(results, kind);
      const rows = [...ranked, ...results.filter(row => row.kind === kind && !ranked.includes(row))];
      if (!rows.length) continue;
      panel.append(element('h4', label));
      const wrapper = element('div', '', 'table-wrap');
      wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', label);
      const table = element('table', ''), header = element('thead', ''), headings = element('tr', ''), body = element('tbody', '');
      for (const title of ['Rank', 'Model', 'Hardware', 'Tokens/s', 'Status', 'Date', 'Details']) {
        const cell = element('th', title); cell.scope = 'col'; headings.append(cell);
      }
      header.append(headings); table.append(header, body);
      for (const row of rows) {
        const item = element('tr', '');
        const rank = ranked.indexOf(row);
        for (const value of [rank < 0 ? '—' : String(rank + 1), row.model, row.target,
          Number.isFinite(row.tokens_per_second) ? row.tokens_per_second.toFixed(1) : '—',
          row.status, new Date(row.timestamp * 1000).toLocaleString()]) item.append(element('td', value));
        const details = element('details', '');
        details.dataset.identity = JSON.stringify([row.model, row.target]);
        details.open = expanded.has(details.dataset.identity);
        details.append(element('summary', 'View details'));
        details.append(element('p', `${row.cases.length} cases · context ${row.context_length} · parallel setting ${row.parallel}`));
        if (row.error) details.append(element('p', row.error));
        if (row.stress) {
          const stress = row.stress;
          if (stress.stop_reason) details.append(element('p', stress.stop_reason === 'failure-scaling-up' ? 'Automatically stopped at the first failed rate while scaling up.' : 'Automatically stopped at the first successful rate while scaling down.'));
          details.append(element('p', `Stress test: ${stress.status} · highest clean tested rate ${stress.best_rpm.toFixed(2)} requests/min · ${stress.round_count} rounds. Observed estimate, not a guarantee of permanent capacity. Latest 200 rounds retained.`));
          for (const round of stress.rounds) details.append(element('p', `${round.rpm.toFixed(2)} requests/min: ${round.successful ? 'passed' : 'failed'} · ${round.completed}/${round.offered} completed · ${round.overflow} overflows · ${round.timeouts} timeouts · ${round.failed} total failures · ${round.wall_seconds.toFixed(1)}s`));
        }
        for (const sample of row.cases) details.append(element('p', `${sample.name}: ${sample.tokens_per_second.toFixed(1)} tokens/s · ${sample.tokens} tokens · ${sample.wall_seconds.toFixed(2)}s wall time · ${sample.timing} timing`));
        const cell = element('td', ''); cell.append(details); item.append(cell); body.append(item);
      }
      wrapper.append(table); panel.append(wrapper);
    }
    for (const row of results.filter(row => !['chat', 'embedding'].includes(row.kind))) {
      panel.append(element('p', `${row.model} · ${row.target} · ${row.status} · ${row.cases.length} completed cases (not ranked). ${row.error || ''}`));
    }
  },
};
if (typeof module !== 'undefined') module.exports = BenchmarkResults;