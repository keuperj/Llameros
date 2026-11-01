'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Ollama editor preserves drafts, confirms recreation and submits typed values', async () => {
  const nodes = new Map(), calls = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', addEventListener(type, callback) { this[type] = callback; }});
    return nodes.get(id);
  };
  const context = vm.createContext({document: {getElementById:get, addEventListener(type, callback) { callback(); }},
    safe: fn => fn, api: async (url, config) => { calls.push({url, config}); return config; }, refresh: async () => {},
    window: {confirm() { calls.push('confirmed'); return true; }}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/ollama.js'), 'utf8'), context);
  vm.runInContext('OllamaConfig.render({ollama_keep_alive:"-1",ollama_flash_attention:true,ollama_kv_cache_type:"q4_0"})', context);
  get('ollama-keep-alive').value = '10m';
  vm.runInContext('OllamaConfig.render({ollama_keep_alive:"-1",ollama_flash_attention:true,ollama_kv_cache_type:"q4_0"})', context);
  assert.equal(get('ollama-keep-alive').value, '10m');
  await get('ollama-form').submit({preventDefault() {}});
  assert.equal(calls[0], 'confirmed');
  assert.equal(calls[1].url, '/api/ollama');
  assert.equal(calls[1].config.ollama_keep_alive, '10m');
  assert.equal(calls[1].config.ollama_flash_attention, true);
  assert.equal(calls[1].config.confirmed, true);
  assert.match(get('ollama-feedback').textContent, /Start Ollama/);
});