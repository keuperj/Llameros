'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../statistics_server/admin.js'), 'utf8');

async function harness({confirm = true, authenticated = false, entries = []} = {}) {
  class Node {
    constructor() { this.value = ''; this.textContent = ''; this.children = []; this.hidden = false; this.listeners = {}; this.type = 'password'; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
  }
  const nodes = new Map(), calls = [], confirmations = [], timers = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  get('status').value = 'pending';
  const context = vm.createContext({
    document:{getElementById:get, createElement:() => new Node()},
    window:{confirm(text) { confirmations.push(text); return confirm; }, addEventListener() {}},
    setInterval() {}, setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {},
    fetch:async (url, config) => {
      const payload = config.body ? JSON.parse(config.body) : null;
      calls.push({url, config, payload});
      let status = 200, data = {};
      if (url.endsWith('/session')) { status = authenticated ? 200 : 401; data = {csrf:'session-csrf'}; }
      else if (url.endsWith('/login')) { authenticated = true; data = {csrf:'new-csrf'}; }
      else if (url.endsWith('/overview')) data = {entries:{pending:entries.length, approved:0, rejected:0}, capacity:100, used:entries.length};
      else if (url.includes('/benchmarks?')) data = {entries, total:entries.length, pending_revision:'a'.repeat(64)};
      else if (url.endsWith('/benchmarks/approve-all')) data = {approved:entries.length, skipped:0};
      else if (url.endsWith('/logout')) authenticated = false;
      return {ok:status === 200, status, json:async () => data};
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return {get, context, calls, confirmations, timers};
}

test('owner login clears credentials, keeps CSRF in memory and loads monitoring', async () => {
  const {get, calls} = await harness();
  get('username').value = 'operator'; get('password').value = 'private-password';
  await get('login-form').listeners.submit({preventDefault() {}});
  const login = calls.find(call => call.url.endsWith('/login'));
  assert.equal(login.payload.username, 'operator');
  assert.equal(login.payload.password, 'private-password');
  assert.equal(get('password').value, ''); assert.equal(get('username').value, '');
  assert.equal(get('dashboard').hidden, false);
  assert.match(get('overview').textContent, /pending/);
  assert.equal(calls.some(call => call.url.endsWith('/vouchers')), false);
});

test('open contributions need no voucher controls or API calls', async () => {
  const {get, calls} = await harness({authenticated:true});
  assert.equal(calls.some(call => call.url.endsWith('/vouchers')), false);
  await get('logout').listeners.click();
  assert.equal(get('dashboard').hidden, true);
});

test('review renders untrusted evidence as text and confirms optimistic status decision', async () => {
  const entries = [{digest:'abc123',status:'pending',flags:['missing-model-digest'], entry:{model:'<img src=x onerror=alert(1)>', hardware:{}, cases:[]}}];
  const {get, calls, confirmations} = await harness({authenticated:true, entries});
  const tr = get('entries').children[0];
  assert.match(tr.children[0].textContent, /<img src=x/);
  assert.equal(tr.children[0].children.length, 0);
  const approve = tr.children[5].children[0];
  await approve.listeners.click();
  const decision = calls.find(call => call.url.endsWith('/benchmarks/abc123'));
  assert.equal(decision.payload.status, 'approved');
  assert.equal(decision.payload.expected_status, 'pending');
  assert.equal(decision.payload.confirmed, true);
  assert.match(confirmations[0], /Approval is not proof/);
});

test('large differences are visually marked for admin review', async () => {
  const entries = [{digest:'different', status:'pending', flags:['disagrees-with-approved-results'], entry:{model:'gemma3:270m', hardware:{}}}];
  const {get} = await harness({authenticated:true, entries});
  const row = get('entries').children[0];
  assert.equal(row.className, 'result-disagreement');
  assert.match(row.children[3].textContent, /disagrees-with-approved-results/);
});

test('approve all confirms entire queue with warnings and sends snapshot revision', async () => {
  const entries = [{digest:'one', status:'pending', flags:['disagrees-with-approved-results'], entry:{hardware:{}}}];
  const {get, calls, confirmations} = await harness({authenticated:true, entries});
  assert.equal(get('approve-all').disabled, false);
  await get('approve-all').listeners.click();
  const call = calls.find(call => call.url.endsWith('/benchmarks/approve-all'));
  assert.equal(call.payload.expected_revision, 'a'.repeat(64));
  assert.equal(call.payload.confirmed, true);
  assert.match(confirmations[0], /ALL pages/);
  assert.match(confirmations[0], /warning flags/);
  assert.match(get('feedback').textContent, /Approved 1/);
  get('status').value = 'approved'; await get('status').listeners.change();
  assert.equal(get('approve-all').hidden, true);
});

test('approve all cancellation or empty queue never posts approval', async () => {
  for (const options of [{confirm:false, entries:[{digest:'one', status:'pending', flags:[], entry:{hardware:{}}}]}, {entries:[]}]) {
    const {get, calls} = await harness({authenticated:true, ...options});
    await get('approve-all').listeners.click();
    assert.equal(calls.some(call => call.url.endsWith('/benchmarks/approve-all')), false);
  }
});