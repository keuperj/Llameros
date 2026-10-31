/**
 * @file Offline Node regression tests for Llameros users dashboard behavior.
 * Uses built-in test/assert and local stubs; no browser, live server, or npm install is needed.
 */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function board() {
  class Node {
    constructor() { this.dataset = {}; this.children = []; this.hidden = false; this.value = ''; this.handlers = {}; this.scrollTop = 0; this.scrollLeft = 0; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(type, callback) { this.handlers[type] = callback; }
    setAttribute(name, value) { this[name] = value; }
    add(option) { this.children.push(option); }
  }
  const nodes = new Map(), calls = [];
  const get = id => { if (!nodes.has(id)) { const node = new Node(); node.id = id; nodes.set(id, node); } return nodes.get(id); };
  const ids = ['user-intro', 'runtime-models', 'key-management', 'usage-history', 'config-menu', 'hardware', 'try-model'];
  const document = {getElementById: get, querySelectorAll: () => ids.map(get), createElement: () => new Node(), addEventListener: (event, fn) => fn()};
  const data = {models: [{name: '<script>model</script>', target: 'cpu'}], keys: [], history: {models: [], samples: []}};
  const context = vm.createContext({document, csrf: 'csrf', modelBoxes: new Map(), runtimeState: null, accessConfig: null, accessContext: null, renderAccess: management => { get('runtime-models').management = management; }, window: {location: {origin: 'https://host'}},
    Option: function(text, value) { this.textContent = text; this.value = value; },
    element: (tag, text) => Object.assign(new Node(), {textContent: text}), clearKeySecret() {},
    renderKeyModels() {}, renderKeyUsage() {}, renderModelUsage() {}, draw() {},
    safe: fn => fn, api: async url => { calls.push(url); return data; }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/users.js'), 'utf8'), context);
  return {get, calls, data, run: source => vm.runInContext(source, context)};
}

test('user board hides administration and clears secrets and prior identity data', () => {
  const ui = board();
  ui.run('configureBoard({username: "alice", role: "user"})');
  assert.equal(ui.get('board-switch').hidden, true);
  assert.equal(ui.get('config-menu').hidden, true);
  assert.equal(ui.get('hardware').hidden, true);
  assert.equal(ui.get('try-model').hidden, true);
  assert.equal(ui.get('key-management').hidden, false);
  assert.equal(ui.get('usage-history').hidden, false);
  assert.equal(ui.get('events').hidden, true);
  assert.equal(ui.get('api-key-owner-label').hidden, true);
  assert.equal(ui.get('api-key-user').value, 'alice');
});

test('admins can switch boards in both directions without changing their role', () => {
  const ui = board();
  ui.run('configureBoard({username: "owner", role: "admin"}, true)');
  assert.equal(ui.get('board-switch').hidden, false);
  assert.equal(ui.get('board-switch').textContent, 'Switch to admin board');
  assert.equal(ui.get('config-menu').hidden, true);
  ui.run('configureBoard(dashboardIdentity, false)');
  assert.equal(ui.get('board-switch').textContent, 'Switch to user board');
  assert.equal(ui.get('config-menu').hidden, false);
  assert.equal(ui.get('events').hidden, false);
  assert.equal(ui.get('api-key-owner-label').hidden, false);
  assert.equal(ui.get('user-intro').hidden, true);
});

test('user refresh uses restricted data and shared access cards without management', async () => {
  const ui = board();
  ui.run('configureBoard({username: "alice", role: "user"})');
  await ui.run('refreshUserBoard()');
  assert.deepEqual(ui.calls, ['/api/user/dashboard']);
  assert.equal(ui.get('runtime-models').management, false);
  assert.equal(ui.run('runtimeState.loaded[0].name'), '<script>model</script>');
  assert.equal(ui.run('accessConfig.secure_url'), 'https://host/inference');
});

test('model search placeholder remains until the first response and is replaced on completion', async () => {
  const ui = board();
  ui.run('configureBoard({username: "alice", role: "user"})');
  assert.equal(ui.get('runtime-models').children[0].textContent, 'Searching for models…');
  assert.equal(ui.get('runtime-models').children[0].role, 'status');
  ui.run('api = () => new Promise(resolve => { globalThis.finishSearch = resolve; })');
  const pending = ui.run('refreshUserBoard()');
  assert.equal(ui.get('runtime-models').children[0].textContent, 'Searching for models…');
  ui.run('finishSearch({models: [], keys: [], history: {models: [], samples: []}})');
  await pending;
  assert.match(ui.get('runtime-models').children[0].textContent, /No models are currently running/);
});

test('failed initial model search replaces searching message without leaking error text', async () => {
  const ui = board();
  ui.run('configureBoard({username: "alice", role: "user"})');
  ui.run('api = async () => { throw new Error("private error"); }');
  await assert.rejects(ui.run('refreshUserBoard()'));
  assert.match(ui.get('runtime-models').children[0].textContent, /Could not search for models/);
  assert.doesNotMatch(ui.get('runtime-models').children[0].textContent, /private error/);
});

test('certificate download visibility follows server policy on either board', () => {
  const ui = board();
  for (const role of ['admin', 'user']) {
    ui.run(`configureBoard({username: 'alice', role: '${role}', certificate_download_enabled: true})`);
    assert.equal(ui.get('certificate-download').hidden, false);
    ui.run(`configureBoard({username: 'alice', role: '${role}', certificate_download_enabled: false})`);
    assert.equal(ui.get('certificate-download').hidden, true);
  }
});

test('user access renders every account as a table row and preserves scrolling', async () => {
  const ui = board();
  ui.run('configureBoard({username: "owner", role: "admin"})');
  ui.data.users = Array.from({length: 15}, (_, index) => ({username: index ? `user-${index}` : 'owner', role: index ? 'user' : 'admin', blocked: index === 2}));
  ui.get('users-scroll').scrollTop = 128;
  await ui.run('refreshUsers()');
  const rows = ui.get('users').children;
  assert.equal(rows.length, 15);
  assert.equal(rows[0].children[0].textContent, 'owner');
  assert.equal(rows[0].children[0].scope, 'row');
  assert.equal(rows[0].children[1].children[0].disabled, true);
  assert.equal(rows[0].children[3].children[0].disabled, true);
  assert.equal(rows[2].children[2].children[0].checked, true);
  assert.equal(rows[14].children[3].children[0]['aria-label'], 'Save access for user-14');
  assert.equal(typeof rows[14].children[3].children[0].handlers.click, 'function');
  assert.equal(ui.get('users-scroll').scrollTop, 128);
});

test('user access table is scrollable after ten rows and identity has a button-like box', () => {
  const root = path.join(__dirname, '../src/llameros/static');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
  assert.match(html, /id="users-scroll" tabindex="0" role="region" aria-labelledby="users-heading"/);
  assert.match(html, /<tbody id="users"><\/tbody>/);
  assert.match(css, /max-height:calc\(var\(--user-header-height\) \+ 10 \* var\(--user-row-height\)\);overflow:auto/);
  assert.match(css, /#identity:not\(:empty\)\{border:1px solid var\(--line\);border-radius:8px;padding:10px 13px/);
});