'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(confirm = true, entries = [{model:'gemma3:270m', tokens_per_second:42}]) {
  const nodes = new Map(), calls = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', checked:false, textContent:'', children:[],
      addEventListener(type, fn) { this[type] = fn; },
      replaceChildren(...items) { this.children = items; }, append(item) { this.children.push(item); }});
    return nodes.get(id);
  };
  const context = vm.createContext({
    sessionEpoch: 1, userBoard:false,
    document:{getElementById:get, addEventListener(type, fn) { fn(); }}, safe:fn => fn,
    element:(tag, textContent) => ({tag, textContent}),
    window:{confirm(text) { calls.push({confirmation:text}); return confirm; }},
    api:async (url, payload) => {
      calls.push({url, payload});
      if (url.endsWith('/certificate')) return {destination:`https://${payload.host}:${payload.port}`, ca_certificate:'public-pem', sha256:'AA:BB:CC'};
      if (url.endsWith('/preview')) return {destination:'https://127.0.0.1:9443', entries, note:'Public results'};
      if (url.endsWith('/share')) return {accepted:1, duplicates:0};
      return payload;
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/benchmark-sharing.js'), 'utf8'), context);
  return {get, context, calls};
}

test('sharing is never automatic; settings render preserves edits and uses typed values', async () => {
  const {get, context, calls} = harness();
  assert.equal(calls.length, 0);
  context.policy = {enabled:false, host:'127.0.0.1', port:9443, ca_certificate:''};
  vm.runInContext('BenchmarkSharing.render(policy)', context);
  assert.equal(get('sharing-enabled').checked, false);
  get('sharing-host').value = '192.0.2.10';
  vm.runInContext('BenchmarkSharing.render(policy)', context);
  assert.equal(get('sharing-host').value, '192.0.2.10');
  get('sharing-port').value = '9444';
  await get('sharing-form').submit({preventDefault() {}});
  assert.equal(calls[0].url, '/api/benchmark-sharing');
  assert.equal(calls[0].payload.port, 9444);
  assert.equal(calls[0].payload.enabled, false);
  assert.equal(calls.some(call => call.url?.endsWith('/share')), false);
});

test('upload requires confirmation showing exact fields, destination and IP caveat', async () => {
  const {get, calls} = harness();
  await get('share-benchmarks').click();
  assert.equal(calls[0].url, '/api/benchmark-sharing/preview');
  assert.match(calls[1].confirmation, /gemma3:270m/);
  assert.match(calls[1].confirmation, /https:\/\/127.0.0.1:9443/);
  assert.match(calls[1].confirmation, /connection IP/);
  assert.equal(calls[2].payload.confirmed, true);
  assert.equal(calls[2].payload.destination, 'https://127.0.0.1:9443');
  assert.equal(calls[1].confirmation.includes('A'.repeat(43)), false);
  assert.deepEqual(calls[2].payload.entries, JSON.parse(get('sharing-preview').textContent).entries);
});

test('cancel or an empty preview sends nothing', async () => {
  for (const [confirmation, entries] of [[false, [{model:'gemma3:270m'}]], [true, []]]) {
    const {get, calls} = harness(confirmation, entries);
    await get('share-benchmarks').click();
    assert.equal(calls.some(call => call.url?.endsWith('/share')), false);
  }
});

test('reset clears private preview and recommendations', () => {
  const {get, context} = harness();
  get('sharing-preview').textContent = 'old payload';
  get('community-models').children.push('old');
  vm.runInContext('BenchmarkSharing.reset()', context);
  assert.equal(get('sharing-preview').textContent, '');
  assert.equal(get('community-models').children.length, 0);
});

test('upload needs no voucher', async () => {
  const {get, calls} = harness();
  await get('share-benchmarks').click();
  const upload = calls.find(call => call.url?.endsWith('/share'));
  assert.ok(upload);
  assert.equal('voucher' in upload.payload, false);
});

test('self-signed trust requires fingerprint confirmation and separate save; never uploads', async () => {
  const {get, calls} = harness();
  get('sharing-host').value = '92.205.18.100'; get('sharing-port').value = '9443';
  await get('sharing-fetch-certificate').click();
  assert.equal(calls[0].url, '/api/benchmark-sharing/certificate');
  assert.match(calls[1].confirmation, /AA:BB:CC/);
  assert.match(calls[1].confirmation, /separate trusted channel/);
  assert.equal(get('sharing-ca').value, 'public-pem');
  assert.equal(calls.filter(call => call.url).length, 1);
  await get('sharing-form').submit({preventDefault() {}});
  assert.equal(calls[2].payload.ca_certificate, 'public-pem');
  assert.equal(calls.some(call => call.url?.endsWith('/share')), false);
});

test('cancelling certificate trust preserves current certificate', async () => {
  const {get} = harness(false);
  get('sharing-host').value = '92.205.18.100'; get('sharing-port').value = '9443';
  get('sharing-ca').value = 'previous';
  await get('sharing-fetch-certificate').click();
  assert.equal(get('sharing-ca').value, 'previous');
});
