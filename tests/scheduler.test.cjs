'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('scheduler editor preserves drafts during polling and explicitly confirms parallelism changes', async () => {
  const nodes = new Map(), calls = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', addEventListener(type, callback) { this[type] = callback; }});
    return nodes.get(id);
  };
  const context = vm.createContext({document: {getElementById:get, addEventListener(type, callback) { callback(); }},
    safe: fn => fn, api: async (url, config) => { calls.push({url, config}); return config; }, refresh: async () => {},
    window: {confirm() { calls.push('confirmed'); return true; }}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/scheduler.js'), 'utf8'), context);
  vm.runInContext('SchedulerConfig.render({queue_size:16,queue_timeout:120,queue_users:0,ollama_num_parallel:2})', context);
  get('scheduler-queue_size').value = '8';
  vm.runInContext('SchedulerConfig.render({queue_size:16,queue_timeout:120,queue_users:0,ollama_num_parallel:2})', context);
  assert.equal(get('scheduler-queue_size').value, '8');
  await get('scheduler-form').submit({preventDefault() {}});
  assert.equal(calls[0].config.queue_size, 8);
  assert.equal(calls[0].config.confirmed, undefined);
  get('scheduler-ollama_num_parallel').value = '4';
  await get('scheduler-form').submit({preventDefault() {}});
  assert.equal(calls[1], 'confirmed');
  assert.equal(calls[2].config.confirmed, true);
});