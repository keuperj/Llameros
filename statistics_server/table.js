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
function draw() {
  const getter = columns[selected][1];
  rows.sort((a, b) => {
    const x = getter(a), y = getter(b);
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * (ascending ? 1 : -1);
  });
  document.querySelectorAll('th').forEach((node, i) => node.setAttribute('aria-sort', i === selected ? (ascending ? 'ascending' : 'descending') : 'none'));
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const [, get] of columns) { const td = document.createElement('td'); td.textContent = get(row); tr.append(td); }
    fragment.append(tr);
  }
  document.getElementById('entries').replaceChildren(fragment);
}
for (const [index, [label]] of columns.entries()) {
  const th = document.createElement('th'), button = document.createElement('button');
  th.scope = 'col'; button.textContent = label;
  button.addEventListener('click', () => { ascending = selected === index ? !ascending : true; selected = index; draw(); });
  th.append(button); document.getElementById('headings').append(th);
}
document.getElementById('load').addEventListener('click', async () => {
  const button = document.getElementById('load'), status = document.getElementById('status');
  button.disabled = true; rows = [];
  try {
    let total = 1;
    while (rows.length < total) {
      const response = await fetch('/api/v1/averages?limit=1000&offset=' + rows.length, {credentials:'omit', referrerPolicy:'no-referrer'});
      if (!response.ok) throw new Error('Service unavailable; retry later.');
      const data = await response.json(); total = data.total;
      rows.push(...data.entries);
      status.textContent = `Loaded ${rows.length} of ${total} entries`;
      if (!data.entries.length) break;
    }
    draw();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
document.getElementById('load').click();