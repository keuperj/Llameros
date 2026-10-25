/**
 * @file Offline Node regression tests for Llameros actions dashboard behavior.
 * Uses built-in test/assert and local stubs; no browser, live server, or npm install is needed.
 */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Feedback = require('../src/llameros/static/actions.js');

function fixture() {
  const controls = [], logs = [], notices = [];
  class Node {
    constructor() { this.dataset = {}; this.parentNode = {}; this.id = ''; this.tagName = 'BUTTON'; }
    setAttribute(key, value) { this[key] = value; }
    insertAdjacentElement(position, badge) { this.nextElementSibling = badge; }
    get classList() { return {contains: value => this.className === value}; }
  }
  const document = {createElement: () => new Node(), querySelectorAll: () => controls};
  const feedback = Feedback.create({document, log: (...args) => logs.push(args), notice: message => notices.push(message)});
  const button = key => { const node = new Node(); controls.push(node); return feedback.bind(node, key); };
  return {feedback, button, logs, notices, controls};
}

test('immediate failure renders safe button-like alert beside exact control and logs once', () => {
  const ui = fixture(), first = ui.button('load:one'), second = ui.button('load:two');
  ui.feedback.fail(first, '<script>Insufficient RAM</script>');
  assert.equal(first.nextElementSibling.textContent, '<script>Insufficient RAM</script>');
  assert.equal(first.nextElementSibling.role, 'alert');
  assert.equal(second.nextElementSibling, undefined);
  assert.equal(ui.logs.length, 1);
  assert.equal(ui.logs[0][1], true);
  ui.feedback.clear(first);
  assert.equal(first.nextElementSibling.hidden, true);
});

test('queued job failure survives rerender, matches ID, and does not duplicate persistent lifecycle log', () => {
  const ui = fixture(), first = ui.button('load:one');
  ui.feedback.track(first, {job_id: 'job-1'});
  ui.feedback.job({job_id: 'other', state: 'failed', error: 'unrelated'});
  assert.equal(ui.logs.length, 0);
  const replacement = ui.button('load:one');
  ui.feedback.job({job_id: 'job-1', state: 'failed', error: 'Insufficient available system RAM'});
  assert.equal(replacement.nextElementSibling.textContent, 'Insufficient available system RAM');
  assert.equal(ui.logs[0][1], false);
  ui.feedback.job({job_id: 'job-1', state: 'failed', error: 'Insufficient available system RAM'});
  assert.equal(ui.logs.length, 1);
  const refreshed = ui.button('load:one');
  assert.equal(refreshed.nextElementSibling.textContent, 'Insufficient available system RAM');
  ui.feedback.reset();
  assert.equal(refreshed.nextElementSibling.hidden, true);
});

test('successful job creates no error; stale failure does not match newer job', () => {
  const ui = fixture(), control = ui.button('generate');
  ui.feedback.track(control, {job_id: 'success'});
  ui.feedback.job({job_id: 'success', state: 'completed'});
  assert.equal(ui.logs.length, 0);
});

test('safe captures actual form submitter before await and attributes failures to it', async () => {
  const ui = fixture(), submitter = ui.button('generate-form');
  const source = fs.readFileSync('/home/keuper/Projekte/Llameros/src/llameros/static/app.js', 'utf8');
  const context = vm.createContext({actionFeedback: ui.feedback});
  vm.runInContext(source.slice(source.indexOf('function actionControl('), source.indexOf('function element(')), context);
  context.event = {currentTarget: {tagName: 'FORM'}, submitter, preventDefault() {}};
  await vm.runInContext("safe(async () => { await Promise.resolve(); event.currentTarget = null; throw new Error('Generation failed'); })(event)", context);
  assert.equal(submitter.nextElementSibling.textContent, 'Generation failed');
});
