'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
const selection = source.slice(source.indexOf('function selectedLoadTargets'), source.indexOf('/**\n * Enable model information'));

function picker() {
  const list = {
    rows: [], contains: () => false,
    append(row) { this.rows.push(row); },
    replaceChildren() { this.rows = []; },
    querySelectorAll(query) {
      const inputs = this.rows.map(row => row.children[0]);
      return query === 'input:checked' ? inputs.filter(input => input.checked) : inputs;
    },
  };
  const label = {};
  const context = vm.createContext({
    $: id => id === 'load-target-options' ? list : label,
    document: {createTextNode: text => text},
    element: () => ({append(...children) { this.children = children; }, addEventListener(_, fn) { this.change = fn; }}),
  });
  vm.runInContext(selection, context);
  return {
    render(slots, multi = [first.id, second.id]) { context.renderLoadTargets({slots, multi_available: multi}); },
    selected() { return Array.from(context.selectedLoadTargets()); },
    toggle(id) {
      const input = list.querySelectorAll('input').find(item => item.value === id);
      input.checked = !input.checked;
      input.change();
    },
    label,
  };
}

const cpu = {id: 'cpu', vendor: 'cpu', label: 'CPU', available: true};
const first = {id: 'gpu-first', vendor: 'nvidia', label: 'GPU 1', available: true};
const second = {id: 'gpu-second', vendor: 'amd', label: 'GPU 2', available: true};
test('initial model hardware defaults to first available GPU, not CPU', () => {
  const control = picker();
  control.render([cpu, first, second]);
  assert.deepEqual(control.selected(), [first.id]);
});
test('multiple GPU selections survive refresh, CPU is exclusive', () => {
  const control = picker();
  control.render([cpu, first, second]);
  control.toggle(second.id);
  control.render([cpu, first, second]);
  assert.deepEqual(control.selected(), [first.id, second.id]);
  assert.equal(control.label.textContent, '2 GPUs selected');
  control.toggle(cpu.id);
  control.render([cpu, first, second]);
  assert.deepEqual(control.selected(), [cpu.id]);
});
test('unavailable GPUs are removed, and GPUs with residents are single-select only', () => {
  const control = picker();
  control.render([cpu, first, second]);
  control.toggle(second.id);
  control.render([cpu, first, second], [second.id]);
  assert.deepEqual(control.selected(), [second.id]);
  control.toggle(first.id);
  assert.deepEqual(control.selected(), [first.id]);
  control.render([cpu, {...first, available: false}, second]);
  assert.deepEqual(control.selected(), [second.id]);
  control.render([cpu]);
  assert.deepEqual(control.selected(), [cpu.id]);
  control.render([]);
  assert.deepEqual(control.selected(), []);
});