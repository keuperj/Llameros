'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function keyPanel(clipboard) {
  class Node {
    constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.dataset = {}; this.handlers = {}; this.value = ''; this.scrollTop = 0; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(event, callback) { this.handlers[event] = callback; }
    focus() { this.focused = true; }
    select() { this.selected = true; }
    get options() { return this.children; }
  }
  const fields = new Map(), calls = [];
  let keys = [];
  const get = id => {
    if (!fields.has(id)) fields.set(id, new Node(id));
    return fields.get(id);
  };
  const context = vm.createContext({$: get, element: (tag, text, cls) => Object.assign(new Node(tag, text), {className: cls}),
    navigator: {clipboard}, csrf: 'csrf', safe: fn => fn,
    api: async (url, body, method) => { calls.push({url, body, method}); return body && !method ? {key: 'llm_test-secret'} : {keys}; },
    refresh: async () => {}});
  const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('function renderKeyModels('), source.indexOf('function renderKeyUsage(')), context);
  vm.runInContext(source.slice(source.indexOf('async function refreshKeys('), source.indexOf("$('storage-form')")), context);
  return {get, calls, setKeys: value => { keys = value; }, run: code => vm.runInContext(code, context)};
}

test('key table renders many keys as rows with accessible right-side delete icons', async () => {
  const ui = keyPanel();
  ui.setKeys(Array.from({length: 12}, (_, index) => ({id: `key-${index}`, name: `<script>${index}</script>`, models: ['*'], expires: 0})));
  ui.get('api-key-table-scroll').scrollTop = 72;
  await ui.run('refreshKeys()');
  const rows = ui.get('api-keys').children;
  assert.equal(rows.length, 12);
  assert.equal(rows[0].tag, 'tr');
  assert.equal(rows[0].children[0].children[0].textContent, '<script>0</script>');
  assert.equal(rows[0].children[2].children[0].textContent, 'active');
  assert.equal(rows[0].children[3].textContent, 'Never');
  const button = rows[0].children.at(-1).children[0];
  assert.equal(button['aria-label'], 'Delete key <script>0</script>');
  assert.equal(button.children[0]['aria-hidden'], 'true');
  assert.equal(ui.get('api-key-table-scroll').scrollTop, 72);
  await button.handlers.click();
  assert.equal(ui.calls[1].url, '/api/keys/key-0');
  assert.equal(ui.calls[1].method, 'DELETE');
});

test('key form submits chosen model, no cap and expiry presets; secret stays masked by markup', async () => {
  for (const days of ['never', '7', '30', '365']) {
    const ui = keyPanel();
    ui.get('api-key-name').value = ' Personal client ';
    ui.get('api-key-models').value = 'gemma3:270m';
    ui.get('api-key-days').value = days;
    await ui.get('api-key-form').handlers.submit();
    assert.equal(ui.calls[0].body.name, 'Personal client');
    assert.equal(ui.calls[0].body.models[0], 'gemma3:270m');
    assert.equal(ui.calls[0].body.max_tokens, null);
    assert.equal(ui.calls[0].body.expires_days, days === 'never' ? null : Number(days));
    assert.equal(ui.get('api-key-secret').value, 'llm_test-secret');
    assert.equal(ui.get('api-key-copy').disabled, false);
    assert.equal(ui.get('api-key-add').open, false);
    assert.equal(ui.get('api-key-result').hidden, false);
  }
});

test('copy and clear use secret without exposing it in feedback; denied clipboard uses masked fallback', async () => {
  let copied = '';
  for (const clipboard of [{writeText: async text => { copied = text; }}, {writeText: async () => { throw new Error('denied'); }}, undefined]) {
    const ui = keyPanel(clipboard);
    ui.get('api-key-secret').value = 'llm_secret';
    await ui.get('api-key-copy').handlers.click();
    assert.ok(!ui.get('api-key-feedback').textContent.includes('llm_secret'));
    if (clipboard && copied) assert.equal(copied, 'llm_secret');
    if (!clipboard || ui.get('api-key-secret').selected) {
      assert.equal(ui.get('api-key-secret').selected, true);
      assert.match(ui.get('api-key-feedback').textContent, /masked/);
    }
    ui.get('api-key-clear').handlers.click();
    assert.equal(ui.get('api-key-secret').value, '');
    assert.equal(ui.get('api-key-copy').disabled, true);
    assert.equal(ui.get('api-key-result').hidden, true);
  }
});

test('model dropdown preserves selection and key status handles no expiry', () => {
  const ui = keyPanel();
  ui.run('renderKeyModels([{name: "one:latest"}, {name: "two:latest"}])');
  assert.equal(ui.get('api-key-models').value, '*');
  ui.get('api-key-models').value = 'two:latest';
  ui.run('renderKeyModels([{name: "one:latest"}])');
  assert.equal(ui.get('api-key-models').value, 'two:latest');
  assert.equal(ui.run('keyState({expires: 0})'), 'active');
  assert.equal(ui.run('keyState({expires: 1})'), 'expired');
  assert.equal(ui.run('keyState({expires: 0, revoked: true})'), 'revoked');
});