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
    panel.replaceChildren();
    if (!results.length) { panel.append(element('p', 'No benchmarks yet. Administrators can benchmark a running model.')); return; }
    for (const [kind, label] of [['chat', 'Chat · output tokens/s'], ['embedding', 'Embedding · input tokens/s']]) {
      const ranked = this.ranked(results, kind);
      if (!ranked.length) continue;
      panel.append(element('h4', label));
      const list = element('ol', '');
      for (const row of ranked) {
        const item = element('li', `${row.model} · ${row.target} · ${row.tokens_per_second.toFixed(1)} tokens/s`);
        const details = element('details', '');
        details.append(element('summary', `${new Date(row.timestamp * 1000).toLocaleString()} · ${row.cases.length} cases · context ${row.context_length} · parallel setting ${row.parallel}`));
        for (const sample of row.cases) details.append(element('p', `${sample.name}: ${sample.tokens_per_second.toFixed(1)} tokens/s · ${sample.tokens} tokens · ${sample.wall_seconds.toFixed(2)}s wall time · ${sample.timing} timing`));
        item.append(details); list.append(item);
      }
      panel.append(list);
    }
    for (const row of results.filter(row => row.status !== 'completed')) {
      panel.append(element('p', `${row.model} · ${row.target} · ${row.status} · ${row.cases.length} completed cases (not ranked). ${row.error || ''}`));
    }
  },
};
if (typeof module !== 'undefined') module.exports = BenchmarkResults;