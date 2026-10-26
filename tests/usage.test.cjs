/**
 * @file Offline Node regression tests for Llameros usage dashboard behavior.
 * Uses built-in test/assert and local stubs; no browser, live server, or npm install is needed.
 */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Usage = require('../src/llameros/static/usage.js');

function panel(columns) {
  class Node {
    constructor(text = '') { this.textContent = text; this.children = []; this.attrs = {}; this.handlers = {}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(event, fn) { this.handlers[event] = fn; }
    setAttribute(name, value) { this.attrs[name] = value; }
  }
  const headers = columns.map(col => new Node(col.field));
  const scroll = {scrollTop: 132, scrollLeft: 40};
  const table = {parentElement: scroll, querySelectorAll: () => headers};
  const body = new Node(); body.closest = () => table;
  // Module uses the browser's document only when building cells/controls.
  global.document = {createElement: () => new Node()};
  const controller = Usage.create(body, columns);
  return {body, headers, scroll, controller, values: index => body.children.map(row => row.children[index].textContent)};
}

for (const [kind, columns] of [['model', Usage.modelColumns], ['key', Usage.keyColumns]]) {
  test(`${kind} usage defaults to output tokens descending, keeps every row and preserves scrolling`, () => {
    const ui = panel(columns);
    const data = Array.from({length: 15}, (_, index) => ({model: `model-${index}`, name: `key-${index}`, output_tokens: index}));
    ui.controller.update(data);
    const output = columns.findIndex(col => col.field === 'output_tokens');
    assert.deepEqual(ui.values(output), Array.from({length: 15}, (_, index) => 14 - index));
    assert.equal(ui.headers[output].attrs['aria-sort'], 'descending');
    assert.equal(ui.body.children.length, 15);
    assert.equal(ui.scroll.scrollTop, 132); assert.equal(ui.scroll.scrollLeft, 40);
    assert.equal(data[0].output_tokens, 0); // Never mutate API data.
  });

  test(`${kind} usage sorts every column in both directions and retains selection on update`, () => {
    for (let index = 0; index < columns.length; index++) {
      const col = columns[index], ui = panel(columns);
      const values = col.numeric ? [10.04, 2.02, 100.01] : ['Zulu', 'alpha', 'Beta'];
      const rows = values.map((value, i) => ({[col.field]: value, output_tokens: i, model: `model-${i}`, name: `key-${i}`}));
      // Set tested fields last so identifier/output columns are tested too.
      rows.forEach((row, i) => { row[col.field] = values[i]; });
      ui.controller.update(rows);
      ui.headers[index].children[0].handlers.click();
      assert.equal(ui.headers[index].attrs['aria-sort'], 'ascending');
      const ordered = col.numeric ? [2.02, 10.04, 100.01] : ['alpha', 'Beta', 'Zulu'];
      const displayed = items => items.map(value => col.decimal ? value.toFixed(1) : value);
      assert.deepEqual(ui.values(index), displayed(ordered));
      ui.headers[index].children[0].handlers.click();
      assert.equal(ui.headers[index].attrs['aria-sort'], 'descending');
      assert.deepEqual(ui.values(index), displayed([...ordered].reverse()));
      ui.controller.update([...rows].reverse());
      assert.deepEqual(ui.values(index), displayed([...ordered].reverse()));
      assert.equal(ui.headers[index].attrs['aria-sort'], 'descending');
    }
  });
}

test('usage sorting is stable for ties and safely renders text and missing numbers', () => {
  const ui = panel(Usage.modelColumns);
  ui.controller.update([{model: '<script>first</script>', output_tokens: 2}, {model: 'second', output_tokens: 2}, {model: 'missing'}]);
  assert.deepEqual(ui.values(0), ['<script>first</script>', 'second', 'missing']);
  assert.deepEqual(ui.values(4), ['0.0', '0.0', '0.0']);
  ui.controller.update([]);
  assert.equal(ui.body.children.length, 0);
});

test('key usage reserves a blank sortable User column and omits unknown usage', () => {
  assert.deepEqual(Usage.keyColumns.map(col => col.field), [
    'name', 'user', 'status', 'requests', 'failed', 'prompt_tokens', 'output_tokens', 'seconds',
    'limits_text', 'user_limits_text',
  ]);
  const ui = panel(Usage.keyColumns);
  ui.controller.update([{name: 'Client', output_tokens: 100, unknown_usage: 12}]);
  assert.equal(ui.body.children[0].children.length, 10);
  assert.deepEqual(ui.values(1), ['']);
  ui.headers[1].children[0].handlers.click();
  assert.equal(ui.headers[1].attrs['aria-sort'], 'ascending');
});