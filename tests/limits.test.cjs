'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Limits = require('../src/llameros/static/limits.js');

test('new keys default to unlimited input/output', () => {
  assert.deepEqual(Limits.draft, {input_k: null, output_k: null, period: 'never'});
  assert.equal(Limits.summary(), 'No limit');
});
test('model policies describe a separate allowance per user, not global consumption', () => {
  const policy = {input_k: 5, output_k: 2, period: 'day', per_user: true, policy_only: true};
  assert.match(Limits.summary(policy), /5k per user/);
  assert.doesNotMatch(Limits.summary(policy), /0k \/|left/);
  assert.match(Limits.summary({...policy, policy_only: false, user: 'alice', input_used: 1000}), /Your model allowance \(alice\)/);
});
test('inherited user allowances are distinguished from explicit overrides', () => {
  const policy = {input_k: 5, output_k: null, period: 'week', inherited: true};
  assert.match(Limits.summary(policy), /^Default user limits/);
  assert.doesNotMatch(Limits.summary({...policy, inherited: false}), /Default user limits/);
});
test('statistics display allowances, remaining tokens and unknown usage honestly', () => {
  const text = Limits.summary({input_k: 2, input_used: 1200, input_remaining: 800, output_k: 1, output_unknown: true, period: 'day', resets_at: 86400});
  assert.match(text, /1.2k \/ 2k; 0.8k left/);
  assert.match(text, /usage unknown; blocked/);
  assert.match(text, /1970-01-02T00:00:00.000Z/);
});