'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');

test('ordinary users can select all models even with no loaded models', () => {
  const select = {value: '', options: [], replaceChildren() { this.options = []; }, append(option) { this.options.push(option); }};
  const context = vm.createContext({dashboardIdentity: {role: 'user'}, $: () => select,
    element: (tag, text) => ({textContent: text})});
  const start = source.indexOf('function renderKeyModels(');
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
  vm.runInContext('renderKeyModels([])', context);
  assert.equal(select.value, '*');
  assert.equal(select.options[0].textContent, 'All models');
  vm.runInContext("renderKeyModels([{name: 'one:latest'}])", context);
  assert.deepEqual(select.options.map(option => option.value), ['*', 'one:latest']);
  select.value = 'one:latest';
  vm.runInContext("renderKeyModels([{name: 'one:latest'}, {name: 'two:latest'}])", context);
  assert.equal(select.value, 'one:latest');
});