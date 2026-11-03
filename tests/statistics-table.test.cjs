'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function row(model, accelerator, speed, kind = 'chat', context = 4096) {
  return {model, kind, hardware:{architecture:'x86_64', logical_cores:16, memory_gib:64,
    accelerator, accelerator_memory_gib:24, unified_memory:false}, context_length:context,
    parallel:1, flash_attention:true, kv_cache_type:'q4_0', timing:'generation',
    tokens_per_second:speed, samples:2, minimum:speed, maximum:speed, protocol:'llameros-six-cases-v2'};
}

async function harness() {
  class Node {
    constructor() { this.children = []; this.value = ''; this.textContent = ''; this.listeners = {}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes.flatMap(node => node.fragment ? node.children : [node]); }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    setAttribute(key, value) { this[key] = value; }
    click() { return this.listeners.click(); }
  }
  const nodes = new Map(), calls = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  let entries = [row('qwen3:8b','nvidia-RTX 4090',80), row('gemma3:4b','cpu',20), row('<img src=x>','cpu',100,'embedding',8192)];
  const context = vm.createContext({
    document:{getElementById:get, createElement:() => new Node(),
      createDocumentFragment:() => Object.assign(new Node(), {fragment:true}),
      querySelectorAll:() => get('headings').children},
    fetch:async (url, config) => {
      calls.push({url, config});
      return {ok:true, json:async () => ({entries, total:entries.length})};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../statistics_server/table.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {get, calls, setEntries(value) { entries = value; }};
}

test('public search and combined filters are local, case insensitive and resettable', async () => {
  const {get, calls} = await harness();
  assert.equal(get('entries').children.length, 3);
  assert.equal(get('entries').children[0].children[0].textContent, '<img src=x>');
  assert.equal(get('entries').children[0].children[0].children.length, 0);
  get('result-search').value = 'QWEN RTX'; get('result-search').listeners.input();
  assert.equal(get('entries').children.length, 1);
  assert.match(get('filter-summary').textContent, /1 of 3/);
  get('filter-accelerator').value = 'cpu'; get('filter-accelerator').listeners.change();
  assert.equal(get('entries').children.length, 0);
  assert.match(get('filter-summary').textContent, /No matching results/);
  get('clear-filters').click();
  assert.equal(get('entries').children.length, 3);
  get('filter-kind').value = 'chat'; get('filter-kind').listeners.change();
  get('filter-context').value = '4096'; get('filter-context').listeners.change();
  assert.equal(get('entries').children.length, 2);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].config.credentials, 'omit');
});

test('sorting respects filters and refresh preserves available selections', async () => {
  const {get, setEntries} = await harness();
  get('filter-kind').value = 'chat'; get('filter-kind').listeners.change();
  get('headings').children[13].children[0].click();
  assert.equal(get('entries').children[0].children[0].textContent, 'gemma3:4b');
  setEntries([row('qwen3:8b','nvidia-RTX 4090',80)]);
  await get('load').click();
  assert.equal(get('filter-kind').value, 'chat');
  assert.equal(get('entries').children.length, 1);
  get('filter-accelerator').value = 'cpu';
  await get('load').click();
  assert.equal(get('filter-accelerator').value, '');
});