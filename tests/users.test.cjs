'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function board() {
  class Node {
    constructor() { this.children = []; this.hidden = false; this.value = ''; this.handlers = {}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(type, callback) { this.handlers[type] = callback; }
    setAttribute() {}
    add(option) { this.children.push(option); }
  }
  const nodes = new Map(), calls = [];
  const get = id => { if (!nodes.has(id)) { const node = new Node(); node.id = id; nodes.set(id, node); } return nodes.get(id); };
  const ids = ['user-intro', 'runtime-models', 'key-management', 'usage-history', 'config-menu', 'hardware', 'try-model'];
  const document = {getElementById: get, querySelectorAll: () => ids.map(get), createElement: () => new Node(), addEventListener: (event, fn) => fn()};
  const data = {models: [{name: '<script>model</script>', target: 'cpu'}], keys: [], history: {models: [], samples: []}};
  const context = vm.createContext({document, csrf: 'csrf', modelBoxes: new Map(), window: {location: {origin: 'https://host'}},
    element: (tag, text) => Object.assign(new Node(), {textContent: text}), clearKeySecret() {},
    renderKeyModels() {}, renderKeyUsage() {}, renderModelUsage() {}, draw() {},
    safe: fn => fn, api: async url => { calls.push(url); return data; }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/users.js'), 'utf8'), context);
  return {get, calls, run: source => vm.runInContext(source, context)};
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

test('user refresh only calls the restricted dashboard and renders model names as text', async () => {
  const ui = board();
  ui.run('configureBoard({username: "alice", role: "user"})');
  await ui.run('refreshUserBoard()');
  assert.deepEqual(ui.calls, ['/api/user/dashboard']);
  const card = ui.get('runtime-models').children[0];
  assert.equal(card.children[0].textContent, '<script>model</script>');
  assert.equal(card.children[2].children[0].value, 'https://host/inference');
  assert.equal(card.children.length, 3); // No model lifecycle controls.
});