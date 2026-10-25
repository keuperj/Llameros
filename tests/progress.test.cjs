/**
 * @file Offline Node regression tests for Llameros progress dashboard behavior.
 * Uses built-in test/assert and local stubs; no browser, live server, or npm install is needed.
 */
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

test('single-model controls block every load while a model is active, but allow unloading it', () => {
  const running = {running: true, loaded: [{name: 'one:latest'}]};
  assert.deepEqual(Progress.modelControls(running, false, 'one:latest'), {load: false, unload: true});
  assert.deepEqual(Progress.modelControls(running, false, 'two:latest'), {load: false, unload: false});
  assert.deepEqual(Progress.modelControls({running: true, loaded: []}, false, 'two:latest'), {load: true, unload: false});
  for (const runtime of [null, {running: false}, {running: true, error: 'unavailable'}]) {
    assert.deepEqual(Progress.modelControls(runtime, false, 'one:latest'), {load: false, unload: false});
  }
  assert.deepEqual(Progress.modelControls(running, true, 'one:latest'), {load: false, unload: false});
});

test('multi-slot controls permit another model only when a hardware slot is free', () => {
  const runtime = {running: true, loaded: [{name: 'one:latest', target: 'cpu'}], slots: [{id: 'cpu', available: false}, {id: 'gpu-one', available: true}]};
  assert.equal(Progress.modelControls(runtime, false, 'two:latest').load, true);
  assert.equal(Progress.modelControls(runtime, false, 'one:latest').unload, true);
  runtime.slots[1].available = false;
  assert.equal(Progress.modelControls(runtime, false, 'two:latest').load, false);
});

test('runtime prominently distinguishes off, running, and named active models without guessing on errors', () => {
  assert.equal(Progress.runtimeState({running: false}).text, 'Ollama off');
  assert.equal(Progress.runtimeState({running: true, loaded: []}).text, 'Ollama running');
  assert.deepEqual(Progress.runtimeState({running: true, loaded: [{name: 'gemma3:270m'}]}), {state: 'model', text: 'Model running — gemma3:270m'});
  assert.match(Progress.runtimeState({running: true, loaded: [{model: 'fallback:latest'}]}).text, /fallback:latest/);
  assert.match(Progress.runtimeState({running: true, loaded: [{name: 'one'}, {name: 'two'}]}).text, /one, two/);
  assert.equal(Progress.runtimeState({running: false, loaded: [{name: 'stale'}]}).state, 'off');
  for (const runtime of [null, {}, {running: false, error: 'Docker down'}]) assert.equal(Progress.runtimeState(runtime).state, 'unknown');
});
test('availability distinguishes cached image, existing container, missing and unknown', () => {
  assert.match(Progress.availability({image_available: true, container_exists: false}).text, /Image available/);
  assert.match(Progress.availability({image_available: false, container_exists: true}).text, /Container available/);
  assert.equal(Progress.availability({image_available: false, container_exists: false}).state, 'missing');
  assert.equal(Progress.availability({error: 'daemon unavailable'}).state, 'unknown');
});