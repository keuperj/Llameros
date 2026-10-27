/**
 * @file Offline Node regression tests for Llameros access dashboard behavior.
 * Uses built-in test/assert and local stubs; no browser, live server, or npm install is needed.
 */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const Access = require('../src/llameros/static/access.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Progress = require('../src/llameros/static/progress.js');

function panel(clipboard) {
  class Node {
    constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.dataset = {}; this.handlers = {}; this.value = ''; }
    append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); if (this.tag === 'select' && !this.value) this.value = node.value; } }
    insertBefore(node, before) { this.children.splice(this.children.indexOf(before), 0, node); node.parent = this; }
    remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(event, handler) { this.handlers[event] = handler; }
    focus() { this.focused = true; }
    select() { this.selected = true; }
  }
  const root = new Node('div');
  const context = vm.createContext({ActionFeedback: {create: () => ({bind() {}, clear() {}, fail() {}, track() {}})}, notice() {}, document: {},document: {getElementById: () => root}, navigator: {clipboard}, ModelAccess: Access,
    element: (tag, text, cls) => Object.assign(new Node(tag, text), {className: cls}), safe: fn => fn});
  const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
  vm.runInContext(source.split('function updateControls()')[0], context);
  return {root, run: code => vm.runInContext(code, context)};
}
function descendants(node, tag) { return node.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...descendants(child, tag)]); }
function start(ui) {
  ui.run('accessConfig = {secure_url: "https://localhost:8443/inference"}; runtimeState = {running: true, loaded: [{name: "one:latest", target: "cpu"}, {name: "one:latest", target: "gpu-test"}]}; renderAccess();');
}
test('user model cards reuse framework examples and copy controls but never offer stop', () => {
  const ui = panel();
  ui.run('accessConfig = {secure_url: "https://host/inference"}; accessContext = 8192; runtimeState = {running: true, loaded: [{name: "<model>:latest", target: "gpu-demo"}]}; renderAccess(false);');
  const card = ui.root.children[0];
  assert.equal(descendants(card, 'h3')[0].textContent, '<model>:latest');
  assert.equal(descendants(card, 'button').some(button => button.dataset.stop), false);
  assert.equal(descendants(card, 'button').some(button => button.dataset.benchmark), false);
  const select = descendants(card, 'select')[0];
  for (const framework of Object.keys(Access.frameworks)) {
    select.value = framework; select.handlers.change();
    const code = descendants(card, 'textarea')[0].value;
    assert.match(code, /LLAMEROS_API_KEY/);
    assert.match(code, /LLAMEROS_CA_CERT/);
    assert.match(code, /gpu-demo/);
  }
  const details = descendants(card, 'details')[0]; details.open = true;
  ui.run('renderAccess(false)');
  assert.equal(ui.root.children[0], card);
  assert.equal(details.open, true);
});
test('one box per model/device, secure examples and preserved state on refresh', () => {
  const ui = panel(); start(ui);
  assert.equal(ui.root.children.length, 2);
  for (const card of ui.root.children) assert.equal(descendants(card, 'button').filter(button => button.dataset.benchmark).length, 1);
  const first = ui.root.children[0], details = descendants(first, 'details')[0]; details.open = true;
  const framework = descendants(first, 'select')[0]; framework.value = 'langchain'; framework.handlers.change();
  assert.match(descendants(first, 'textarea')[0].value, /ChatOllama/);
  assert.match(descendants(ui.root.children[1], 'textarea')[0].value, /gpu-test/);
  ui.run('renderAccess()');
  assert.equal(ui.root.children[0], first); assert.equal(details.open, true);
  ui.run('runtimeState.loaded.pop(); renderAccess()'); assert.equal(ui.root.children.length, 1);
  ui.run('runtimeState.running = false; renderAccess()'); assert.equal(ui.root.children.length, 0);
});
test('per-model copy supports clipboard and manual fallback', async () => {
  let copied;
  for (const available of [true, false]) {
    const ui = panel(available ? {writeText: async value => { copied = value; }} : undefined); start(ui);
    const box = ui.root.children[0], code = descendants(box, 'textarea')[0];
    await descendants(box, 'button').at(-1).handlers.click();
    if (available) assert.equal(copied, code.value);
    else { assert.equal(code.focused, true); assert.equal(code.selected, true); }
  }
});

test('each framework uses the configured host endpoint, active model and valid Python', () => {
  for (const framework of Object.keys(Access.frameworks)) {
    const example = Access.example(framework, 'gemma3:270m', 'http://127.0.0.1:12567/', 2048);
    assert.match(example.install, /^python -m pip install /);
    assert.ok(example.code.includes('gemma3:270m'));
    assert.ok(example.code.includes('http://127.0.0.1:12567'));
    assert.ok(!example.code.includes('11434'));
    if (framework === 'openai') assert.match(example.code, /12567\/v1/);
    else assert.ok(example.code.includes('2048'));
    const result = spawnSync('python3', ['-c', 'import ast,sys; ast.parse(sys.stdin.read())'], {input: example.code, encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
  }
});

test('missing selections and unknown frameworks do not produce examples', () => {
  assert.equal(Access.example('unknown', 'model', 'http://127.0.0.1:11435'), null);
  assert.equal(Access.example('ollama', '', 'http://127.0.0.1:11435'), null);
  assert.equal(Access.example('ollama', 'model', ''), null);
  assert.equal(Access.example('__proto__', 'model', 'http://127.0.0.1:11435'), null);
});

test('model names are quoted rather than interpolated as Python code', () => {
  const example = Access.example('ollama', 'test"\\name\n', 'http://127.0.0.1:11435', -1);
  const result = spawnSync('python3', ['-c', 'import ast,sys; ast.parse(sys.stdin.read())'], {input: example.code, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  assert.ok(example.code.includes('4096'));
});

test('secure examples use bearer credentials, TLS trust and explicit hardware routing', () => {
  for (const framework of Object.keys(Access.frameworks)) {
    const example = Access.example(framework, 'gemma3:270m', 'https://127.0.0.1:8443/inference', 4096, 'gpu-0123456789abcdef');
    assert.match(example.code, /LLAMEROS_API_KEY/);
    assert.match(example.code, /LLAMEROS_CA_CERT/);
    assert.match(example.code, /X-Llameros-Target/);
    assert.match(example.code, /gpu-0123456789abcdef/);
    assert.ok(!example.code.includes('ignores it'));
    assert.ok(!example.code.includes('verify=False'));
    const result = spawnSync('python3', ['-c', 'import ast,sys; ast.parse(sys.stdin.read())'], {input: example.code, encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
  }
});

test('per-model access never exposes direct runtime URLs', () => {
  const ui = panel(); start(ui);
  ui.run('runtimeState.loaded[0].base_url = "http://127.0.0.1:11435"; renderAccess()');
  const code = descendants(ui.root.children[0], 'textarea')[0].value;
  assert.match(code, /LLAMEROS_API_KEY/); assert.ok(!code.includes('11435'));
});
