'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
const selection = source.slice(source.indexOf("    const targetPrevious = $('load-target').value;"), source.indexOf('    const runtimeStatus = JobProgress.runtimeState'));

function select(slots, previous = '') {
  let value = previous;
  const control = {
    options: [],
    get value() { return value; },
    set value(next) { value = next; },
  };
  vm.runInNewContext(selection, {
    $: () => control, data: {ollama: {slots}}, busy: false,
    options(node, values) {
      node.options = values.map(value => ({value}));
      node.value = values[0] || '';
    },
  });
  return control;
}

const cpu = {id: 'cpu', vendor: 'cpu', label: 'CPU', available: true};
const first = {id: 'gpu-first', vendor: 'nvidia', label: 'GPU 1', available: true};
const second = {id: 'gpu-second', vendor: 'amd', label: 'GPU 2', available: true};
test('initial model hardware defaults to first available GPU, not CPU', () => {
  assert.equal(select([cpu, first, second]).value, first.id);
});
test('explicit CPU and GPU selections survive refresh', () => {
  assert.equal(select([cpu, first, second], 'cpu').value, 'cpu');
  assert.equal(select([cpu, first, second], second.id).value, second.id);
});
test('CPU is the fallback only when no GPU is available', () => {
  assert.equal(select([cpu, {...first, available: false}]).value, 'cpu');
  assert.equal(select([cpu, {...first, available: false}, second], first.id).value, second.id);
  assert.equal(select([]).disabled, true);
});