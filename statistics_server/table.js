'use strict';
const columns = [
  ['Model', r => r.model], ['Kind', r => r.kind], ['Architecture', r => r.hardware.architecture],
  ['Core bucket', r => r.hardware.logical_cores], ['RAM GiB bucket', r => r.hardware.memory_gib],
  ['Accelerator class', r => r.hardware.accelerator], ['VRAM GiB bucket', r => r.hardware.accelerator_memory_gib],
  ['Unified memory', r => String(r.hardware.unified_memory)], ['Context', r => r.context_length],
  ['Parallel', r => r.parallel], ['Flash Attention', r => String(r.flash_attention)],
  ['KV cache', r => r.kv_cache_type], ['Timing', r => r.timing], ['Tokens/s', r => r.tokens_per_second],
  ['Samples', r => r.samples], ['Minimum', r => r.minimum], ['Maximum', r => r.maximum], ['Protocol', r => r.protocol],
  ['Artifact digest', r => r.model_digest || 'unknown'],
];
let rows = [], selected = 13, ascending = false;
const filters = [
  ['filter-kind', r => r.kind, 'All workloads'],
  ['filter-accelerator', r => r.hardware.accelerator, 'All accelerators'],
  ['filter-architecture', r => r.hardware.architecture, 'All architectures'],
  ['filter-timing', r => r.timing, 'All timing methods'],
  ['filter-context', r => r.context_length, 'All context sizes'],
  ['filter-cache', r => r.kv_cache_type, 'All cache types'],
];
function populateFilters() {
  for (const [id, getter, label] of filters) {
    const select = document.getElementById(id), previous = select.value;
    const values = [...new Set(rows.map(row => String(getter(row))))].sort((a, b) => a.localeCompare(b, undefined, {numeric:true}));
    const options = ['', ...values].map(value => {
      const option = document.createElement('option'); option.value = value; option.textContent = value || label; return option;
    });
    select.replaceChildren(...options);
    select.value = values.includes(previous) ? previous : '';
  }
}
function draw() {
  const terms = document.getElementById('result-search').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const visible = rows.filter(row => {
    if (!filters.every(([id, getter]) => !document.getElementById(id).value || String(getter(row)) === document.getElementById(id).value)) return false;
    const text = columns.map(([, get]) => get(row)).join(' ').toLowerCase();
    return terms.every(term => text.includes(term));
  });
  const getter = columns[selected][1];
  visible.sort((a, b) => {
    const x = getter(a), y = getter(b);
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * (ascending ? 1 : -1);
  });
  document.querySelectorAll('th').forEach((node, i) => node.setAttribute('aria-sort', i === selected ? (ascending ? 'ascending' : 'descending') : 'none'));
  const fragment = document.createDocumentFragment();
  for (const row of visible) {
    const tr = document.createElement('tr');
    for (const [, get] of columns) { const td = document.createElement('td'); td.textContent = get(row); tr.append(td); }
    fragment.append(tr);
  }
  document.getElementById('entries').replaceChildren(fragment);
  document.getElementById('filter-summary').textContent = `${visible.length} of ${rows.length} approved averages shown${visible.length ? '.' : '. No matching results; clear or change your filters.'}`;
}
document.getElementById('result-filters').addEventListener('submit', event => event.preventDefault());
document.getElementById('result-search').addEventListener('input', draw);
for (const [id] of filters) document.getElementById(id).addEventListener('change', draw);
document.getElementById('clear-filters').addEventListener('click', () => {
  document.getElementById('result-search').value = '';
  for (const [id] of filters) document.getElementById(id).value = '';
  draw();
});
for (const [index, [label]] of columns.entries()) {
  const th = document.createElement('th'), button = document.createElement('button');
  th.scope = 'col'; button.textContent = label;
  button.addEventListener('click', () => { ascending = selected === index ? !ascending : true; selected = index; draw(); });
  th.append(button); document.getElementById('headings').append(th);
}
document.getElementById('load').addEventListener('click', async () => {
  const button = document.getElementById('load'), status = document.getElementById('status');
  button.disabled = true;
  try {
    let total = 1; const loaded = [];
    while (loaded.length < total) {
      const response = await fetch('/api/v1/averages?limit=1000&offset=' + loaded.length, {credentials:'omit', referrerPolicy:'no-referrer'});
      if (!response.ok) throw new Error('Service unavailable; retry later.');
      const data = await response.json(); total = data.total;
      loaded.push(...data.entries);
      status.textContent = `Loaded ${loaded.length} of ${total} approved averages`;
      if (!data.entries.length) break;
    }
    rows = loaded; populateFilters(); draw();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
document.getElementById('load').click();