'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');

test('GPU history uses busiest valid measurement and leaves unknown samples as gaps', () => {
  const points = [];
  const ctx = {clearRect() {}, beginPath() {}, stroke() {}, fillText() {},
    moveTo(x, y) { if (this.strokeStyle === '#83bfff') points.push(['move', y]); },
    lineTo(x, y) { if (this.strokeStyle === '#83bfff') points.push(['line', y]); }};
  const context = vm.createContext({$: () => ({width: 1100, height: 180, getContext: () => ctx})});
  vm.runInContext(source.slice(source.indexOf('function draw(samples)'), source.lastIndexOf("safe(async () =>")), context);
  context.samples = [
    {hardware: {gpus: [{utilization: 20}, {utilization: 80}]}},
    {hardware: {gpus: [{utilization: null}]}},
    {hardware: {gpus: [{utilization: 0}]}},
    {hardware: {gpus: [{utilization: 100}]}}
  ];
  vm.runInContext('draw(samples)', context);
  assert.deepEqual(points, [['move', 44], ['move', 160], ['line', 15]]);
});