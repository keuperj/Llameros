'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const results = require('../src/llameros/static/benchmarks.js');

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