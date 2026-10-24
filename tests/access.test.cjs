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
  const fields = new Map();
  const get = id => {
    if (!fields.has(id)) fields.set(id, {value: '', options: [], textContent: '', handlers: {},
      addEventListener(event, callback) { this.handlers[event] = callback; },
      focus() { this.focused = true; }, select() { this.selected = true; }});
    return fields.get(id);
  };
  const copies = ['access-url', 'access-openai-url', 'access-install', 'access-code'].map(id => ({...get('copy-' + id), dataset: {copy: id}}));
  get('access-framework').value = 'ollama';
  const context = vm.createContext({document: {getElementById: get, querySelectorAll: () => copies},
    navigator: {clipboard}, ModelAccess: Access, JobProgress: Progress,
    options(select, names) { select.options = names.map(value => ({value})); select.value = names[0] || ''; }});
  const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
  vm.runInContext(source.split('function updateControls()')[0], context);
  return {get, copies, context, run: code => vm.runInContext(code, context)};
}

test('access panel preserves selection and clears code when runtime stops or model unloads', () => {
  const ui = panel();
  ui.run('accessConfig = {base_url: "http://127.0.0.1:12567", openai_url: "http://127.0.0.1:12567/v1"}; runtimeState = {running: true, loaded: [{name: "one:latest"}, {name: "two:latest"}]}; renderAccess();');
  assert.match(ui.get('access-code').value, /one:latest/);
  ui.get('access-active-model').value = 'two:latest';
  ui.get('access-active-model').handlers.change();
  ui.run('renderAccess()');
  assert.equal(ui.get('access-active-model').value, 'two:latest');
  assert.match(ui.get('access-code').value, /two:latest/);
  ui.get('access-framework').value = 'langchain';
  ui.get('access-framework').handlers.change();
  assert.match(ui.get('access-code').value, /ChatOllama/);
  ui.run('runtimeState.loaded = []; renderAccess()');
  assert.equal(ui.get('access-code').value, '');
  assert.equal(ui.get('access-active-model').disabled, true);
  assert.equal(ui.copies[3].disabled, true);
  assert.equal(ui.copies[0].disabled, false); // Endpoint still useful when stopped.
  ui.run('runtimeState = {running: false}; renderAccess()');
  assert.match(ui.get('access-state').textContent, /stopped or unavailable/);
  ui.run('accessConfig = null; renderAccess()');
  assert.equal(ui.get('access-url').value, '');
  assert.equal(ui.copies[0].disabled, true);
});

test('copy buttons write the displayed value or select it for manual copying', async () => {
  let copied = '';
  const ui = panel({writeText: async text => { copied = text; }});
  ui.get('access-code').value = 'print("Hello")';
  await ui.copies[3].handlers.click();
  assert.equal(copied, 'print("Hello")');
  assert.equal(ui.get('access-feedback').textContent, 'Copied to clipboard.');
  const fallback = panel();
  fallback.get('access-code').value = 'print("Hello")';
  await fallback.copies[3].handlers.click();
  assert.equal(fallback.get('access-code').focused, true);
  assert.equal(fallback.get('access-code').selected, true);
  assert.match(fallback.get('access-feedback').textContent, /Ctrl\+C/);
});

test('same model on two devices selects the corresponding endpoint and hardware', () => {
  const ui = panel();
  ui.run('accessConfig = {base_url: "http://127.0.0.1:11435", openai_url: "http://127.0.0.1:11435/v1"}; runtimeState = {running: true, loaded: [{name: "one:latest", target: "cpu", hardware: "CPU", base_url: "http://127.0.0.1:11435"}, {name: "one:latest", target: "gpu-test", hardware: "GPU 1", base_url: "http://127.0.0.1:11436"}]}; renderAccess();');
  assert.equal(ui.get('access-active-model').options.length, 2);
  ui.get('access-active-model').value = JSON.stringify(['one:latest', 'gpu-test']);
  ui.get('access-active-model').handlers.change();
  assert.equal(ui.get('access-url').value, 'http://127.0.0.1:11436');
  assert.match(ui.get('access-code').value, /11436/);
  assert.match(ui.get('access-code').value, /one:latest/);
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