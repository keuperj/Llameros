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
  get('status').value = 'pending'; get('max-entries').value = '3'; get('hours').value = '24';
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
      else if (url.endsWith('/overview')) data = {entries:{pending:entries.length, approved:0, rejected:0}, capacity:100, used:entries.length, vouchers:{available:1, redeemed:0}};
      else if (url.includes('/benchmarks?')) data = {entries, total:entries.length};
      else if (url.endsWith('/vouchers')) data = {voucher:'secret-voucher', max_entries:3, hours:24};
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

test('voucher issuance requires confirmation and clears secret on expiry and logout', async () => {
  const {get, calls, timers, confirmations} = await harness({authenticated:true});
  await get('voucher-form').listeners.submit({preventDefault() {}});
  const issued = calls.find(call => call.url.endsWith('/vouchers'));
  assert.equal(issued.payload.max_entries, 3); assert.equal(issued.payload.hours, 24);
  assert.equal(issued.payload.confirmed, true);
  assert.equal(issued.config.headers['X-CSRF-Token'], 'session-csrf');
  assert.equal(get('voucher-secret').value, 'secret-voucher');
  assert.match(confirmations[0], /no recipient identity/);
  timers[0](); assert.equal(get('voucher-secret').value, '');
  get('voucher-secret').value = 'secret';
  await get('logout').listeners.click();
  assert.equal(get('voucher-secret').value, '');
  assert.equal(get('dashboard').hidden, true);
  assert.equal(get('entries').children.length, 0);
});

test('cancelling voucher confirmation does not issue permission', async () => {
  const {get, calls} = await harness({authenticated:true, confirm:false});
  await get('voucher-form').listeners.submit({preventDefault() {}});
  assert.equal(calls.some(call => call.url.endsWith('/vouchers')), false);
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