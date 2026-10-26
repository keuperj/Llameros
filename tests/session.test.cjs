/** Delayed account/board responses must never reach their rendering callers. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/llameros/static/app.js'), 'utf8');

for (const active of [true, false]) test(`session rejection banner with active=${active}`, async () => {
  const banner = {hidden: true};
  const context = vm.createContext({sessionEpoch: 1, csrf: active ? 'token' : '',
    $: () => banner, signedOut() { context.csrf = ''; },
    fetch: async () => ({ok: false, status: 401, json: async () => ({detail: 'Unauthorized'})})});
  vm.runInContext(source.slice(source.indexOf('async function api('), source.indexOf('function actionControl(')), context);
  await assert.rejects(vm.runInContext("api('/api/session')", context));
  assert.equal(banner.hidden, !active);
});

for (const status of [200, 401, 503]) test(`old response ${status} is discarded after account/board change`, async () => {
  let finish;
  const context = vm.createContext({sessionEpoch: 1, csrf: 'old', signedOut() { throw new Error('must not sign out new account'); },
    fetch: () => new Promise(resolve => { finish = resolve; })});
  vm.runInContext(source.slice(source.indexOf('async function api('), source.indexOf('function actionControl(')), context);
  const pending = vm.runInContext("api('/api/keys')", context);
  context.sessionEpoch++;
  finish({ok: status === 200, status, json: async () => ({secret: 'old secret'})});
  await assert.rejects(pending, error => error.staleSession === true && error.message === '');
});

test('old handler failure cannot repopulate feedback after switching accounts', async () => {
  let reject;
  const context = vm.createContext({sessionEpoch: 1, actionFeedback: {clear() {}, fail() { throw new Error('stale feedback'); }},
    work: () => new Promise((resolve, fail) => { reject = fail; })});
  vm.runInContext(source.slice(source.indexOf('function actionControl('), source.indexOf('function element(')), context);
  const pending = vm.runInContext('safe(work)()', context);
  context.sessionEpoch++;
  reject(new Error('old account secret'));
  await pending;
});