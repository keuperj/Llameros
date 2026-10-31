'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const results = require('../src/llameros/static/benchmarks.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('rankings render tables with expandable details preserved across refresh', () => {
  class Node {
    constructor(tag, text) { this.tag = tag; this.textContent = text; this.children = []; this.dataset = {}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren() { this.children = []; }
    setAttribute(name, value) { this[name] = value; }
    querySelectorAll() { return descendants(this).filter(node => node.tag === 'details' && node.open); }
  }
  const descendants = node => node.children.flatMap(child => [child, ...descendants(child)]);
  const panel = new Node('div', '');
  const context = vm.createContext({document: {getElementById: () => panel}, element: (tag, text) => new Node(tag, text)});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/benchmarks.js'), 'utf8'), context);
  context.rows = [{model: '<model>', target: 'cpu', kind: 'chat', status: 'completed', tokens_per_second: 42,
    timestamp: 1, context_length: 4096, parallel: 2, cases: [],
    stress: {status:'stopped', best_rpm:8, round_count:1, rounds:[{rpm:8,successful:true,completed:6,offered:6,overflow:0,timeouts:0,failed:0,wall_seconds:60}]}},
    {model: 'failed', target: 'cpu', kind: 'chat', status: 'failed', tokens_per_second: null, timestamp: 1, cases: [], error: 'Runtime error'}];
  vm.runInContext('BenchmarkResults.render(rows)', context);
  assert.equal(descendants(panel).filter(node => node.tag === 'table').length, 1);
  assert.equal(descendants(panel).filter(node => node.tag === 'th').length, 7);
  assert.equal(descendants(panel).filter(node => node.tag === 'details').length, 2);
  assert.ok(descendants(panel).some(node => node.tag === 'td' && node.textContent === '<model>'));
  assert.ok(descendants(panel).some(node => node.tag === 'p' && node.textContent.includes('highest clean tested rate 8.00')));
  descendants(panel).find(node => node.tag === 'details').open = true;
  vm.runInContext('BenchmarkResults.render(rows)', context);
  assert.equal(descendants(panel).find(node => node.tag === 'details').open, true);
});

test('manual benchmark stop remains available while other lifecycle controls are disabled', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');
  assert.match(source, /job.action === 'benchmark' && busy/);
  assert.match(source, /Stop benchmark/);
  assert.match(source, /api\('\/api\/benchmark\/stop', \{\}\)/);
  assert.doesNotMatch(source, /up to 5 minutes/);
});

test('benchmark panel is a standalone playground-style card, visible to users', () => {
  const root = path.join(__dirname, '../src/llameros/static');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.match(html, /<details id="benchmark-history" class="card playground-menu">/);
  assert.ok(html.indexOf('id="benchmark-history"') < html.indexOf('id="usage-history"'));
  const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
  assert.match(css, /\.running-model-actions\{[^}]*justify-content:flex-end;[^}]*margin-left:auto/);
});

test('rankings separate workloads, sort descending and exclude incomplete or unknown measurements', () => {
  const rows = [
    {kind: 'chat', status: 'completed', tokens_per_second: 12},
    {kind: 'embedding', status: 'completed', tokens_per_second: 1000},
    {kind: 'chat', status: 'timed-out', tokens_per_second: 500},
    {kind: 'chat', status: 'completed', tokens_per_second: null},
    {kind: 'chat', status: 'completed', tokens_per_second: 40},
  ];
  assert.deepEqual(results.ranked(rows, 'chat').map(row => row.tokens_per_second), [40, 12]);
  assert.equal(results.ranked(rows, 'embedding').length, 1);
});