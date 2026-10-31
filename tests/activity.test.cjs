'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('activity renders safe tables, filters failures and keeps acknowledgement warning', () => {
  class Node {
    constructor(text = '') { this.textContent = text; this.children = []; this.scrollTop = 35; this.scrollLeft = 12; }
    append(node) { this.children.push(node); }
    replaceChildren() { this.children = []; }
    closest() { return this; }
    setAttribute(key, value) { this[key] = value; }
  }
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const context = vm.createContext({document: {getElementById: get, addEventListener() {}},
    element: (tag, text, className) => Object.assign(new Node(text), {tag, className})});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/activity.js'), 'utf8'), context);
  context.data = {alert: {failures: 3, acknowledged: 1}, request_counts: {total: 2, successful: 1, failed: 1},
    admin_counts: {total: 0, successful: 0, failed: 0}, requests: [
      {timestamp: 1, username: '<script>', status: 'completed', tokens_per_second: 10},
      {timestamp: 2, status: 'failed', tokens_per_second: null}]};
  vm.runInContext('ActivityLog.render(data)', context);
  assert.equal(get('activity-warning').hidden, false);
  assert.match(get('activity-warning-text').textContent, /^2 failed/);
  assert.equal(get('activity-users').children[0].children[1].textContent, '<script>');
  assert.equal(get('activity-users').children[1].className, 'activity-error');
  assert.equal(get('activity-users').scrollTop, 35);
  vm.runInContext('ActivityLog.userErrors = true; ActivityLog.render(data)', context);
  assert.equal(get('activity-users').children.length, 1);
  assert.equal(get('activity-user-filter')['aria-pressed'], 'true');
  vm.runInContext("ActivityLog.adminErrors = true; ActivityLog.events([{kind:'load:completed'}, {kind:'load:failed'}])", context);
  assert.equal(get('events').children.length, 1);
  assert.equal(get('events').children[0].className, 'activity-error');
  vm.runInContext('ActivityLog.reset()', context);
  assert.equal(get('activity-warning').hidden, true);
  assert.equal(get('activity-users').children.length, 0);
});