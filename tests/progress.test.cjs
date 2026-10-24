'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Progress = require('../src/llameros/static/progress.js');

test('state controls enable only opposite container actions and expose only active models', () => {
  assert.equal(Progress.controls(null, false).start, false);
  assert.equal(Progress.controls({running: false}, false).start, true);
  assert.equal(Progress.controls({running: false}, false).stop, false);
  const running = {running: true, loaded: [{name: 'active:latest'}], models: [{name: 'inactive:latest'}]};
  assert.equal(Progress.controls(running, false).start, false);
  assert.equal(Progress.controls(running, false).stop, true);
  assert.deepEqual(Progress.controls(running, false).active, ['active:latest']);
  assert.equal(Progress.controls(running, true).stop, false);
  assert.deepEqual(Progress.controls({running: false, loaded: running.loaded}, false).active, []);
  assert.equal(Progress.controls({running: false, error: 'unknown'}, false).start, false);
});

test('real byte counters yield percentages and missing counters remain indeterminate', () => {
  assert.equal(Progress.percent({completed: 25, total: 100}), 25);
  assert.equal(Progress.percent({completed: 300, total: 100}), 100);
  assert.equal(Progress.percent({completed: -3, total: 100}), 0);
  for (const value of [{}, {total: 0}, {total: 100}, {completed: null, total: 100}]) assert.equal(Progress.percent(value), null);
});
test('availability distinguishes cached image, existing container, missing and unknown', () => {
  assert.match(Progress.availability({image_available: true, container_exists: false}).text, /Image available/);
  assert.match(Progress.availability({image_available: false, container_exists: true}).text, /Container available/);
  assert.equal(Progress.availability({image_available: false, container_exists: false}).state, 'missing');
  assert.equal(Progress.availability({error: 'daemon unavailable'}).state, 'unknown');
});