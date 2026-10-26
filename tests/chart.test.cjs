'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');

test('GPU history tracks each device across reorderings and leaves unknown samples as gaps', () => {
  const points = [];
  const ctx = {clearRect() {}, beginPath() {}, stroke() {}, fillText() {},
    moveTo(x, y) { if (this.strokeStyle === '#83bfff') points.push(['move', y]); },
    lineTo(x, y) { if (this.strokeStyle === '#83bfff') points.push(['line', y]); }};
  const legend = {children: [], replaceChildren() { this.children = []; }, append(node) { this.children.push(node); }};
  const context = vm.createContext({$: id => id === 'chart-legend' ? legend : ({width: 1100, height: 180, getContext: () => ctx}),
    element: (tag, text) => ({textContent: text, style: {}})});
  vm.runInContext(source.slice(source.indexOf('function draw(samples)'), source.lastIndexOf("safe(async () =>")), context);
  context.samples = [
    {hardware: {gpus: [{id: 'a', utilization: 20}, {id: 'b', utilization: 80}]}},
    {hardware: {gpus: [{id: 'b', utilization: 50}, {id: 'a', utilization: null}]}},
    {hardware: {gpus: [{id: 'b', utilization: 90}, {id: 'a', utilization: 0}]}},
    {hardware: {gpus: [{id: 'a', utilization: 100}]}}
  ];
  vm.runInContext('draw(samples)', context);
  assert.deepEqual(points, [['move', 131], ['move', 160], ['line', 15]]);
  assert.deepEqual(legend.children.map(node => node.textContent), ['● CPU', '● RAM', '● GPU 1', '● GPU 2']);
  assert.notEqual(legend.children[2].style.color, legend.children[3].style.color);
  vm.runInContext('draw([])', context);
  assert.equal(legend.children.length, 2);
});