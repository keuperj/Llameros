'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Catalogue = require('../src/llameros/static/catalogue.js');

test('model info links point to the selected official family or variant', () => {
  assert.equal(Catalogue.modelInfoUrl('gemma3'), 'https://ollama.com/library/gemma3');
  assert.equal(Catalogue.modelInfoUrl('smollm2:135m-instruct-q2_K'), 'https://ollama.com/library/smollm2:135m-instruct-q2_K');
  for (const value of ['', '../escape', 'https://evil.example', 'gemma3\n', 'gemma3:<script>']) {
    assert.equal(Catalogue.modelInfoUrl(value), null);
  }
});

test('compatible entries first, popularity order retained within each group', () => {
  const entries = [{name: 'unknown', rank: 0, fits: null}, {name: 'large', rank: 1, fits: false},
    {name: 'popular-fit', rank: 2, fits: true}, {name: 'other-fit', rank: 3, fits: true}];
  assert.deepEqual(Catalogue.sort(entries).map(e => e.name), ['popular-fit', 'other-fit', 'large', 'unknown']);
  assert.equal(entries[0].name, 'unknown');
});

test('recommendations include only fitting families/variants, prefer fitting latest, cap at six', () => {
  const small = {name: 'small:1b', fits: true}, latest = {name: 'small:latest', fits: true};
  const entries = Array.from({length: 8}, (_, rank) => ({name: 'family' + rank, rank, fits: true, variants: [small, latest]}));
  entries.push({name: 'unknown', rank: -1, fits: null, variants: []});
  entries.push({name: 'large', rank: -2, fits: false, variants: [{fits: false}]});
  const recommendations = Catalogue.recommendations(entries.reverse());
  assert.equal(recommendations.length, 6);
  assert.deepEqual(recommendations.map(e => e.rank), [0, 1, 2, 3, 4, 5]);
  assert.equal(recommendations[0].variant.name, 'small:latest');
  assert.equal(Catalogue.recommendations([{name: 'small', rank: 0, fits: true,
    variants: [small, {name: 'small:latest', fits: false}]}])[0].variant, small);
});

test('labels distinguish estimated fits, oversized and unknown without relying solely on color', () => {
  assert.match(Catalogue.label({name: 'fit', fits: true}), /^● fit — estimated fit$/);
  assert.match(Catalogue.label({name: 'large', fits: false}), /exceeds estimate/);
  assert.match(Catalogue.label({name: 'unknown', fits: null}), /unchecked \/ unknown/);
});

test('CPU-only filters families and tags, excludes unknown/oversized, keeps fitting CPU variants', () => {
  const entries = [
    {name: 'smollm2', fits: true, rank: 0, variants: [
      {name: 'smollm2:135m', fits: true, cpu_fits: true},
      {name: 'smollm2:huge', fits: false, cpu_fits: false}]},
    {name: 'unknown', rank: 1, variants: [{name: 'unknown:latest', cpu_fits: null}]},
  ];
  const result = Catalogue.visible(entries, '', true);
  assert.deepEqual(result.map(e => e.name), ['smollm2']);
  assert.deepEqual(result[0].variants.map(v => v.name), ['smollm2:135m']);
  assert.equal(entries[0].variants.length, 2);
  assert.equal(Catalogue.visible(entries, '', false).length, 2);
  assert.equal(Catalogue.visible(entries, 'SMOLLM2:135M', true).length, 1);
  assert.equal(Catalogue.visible(entries, 'smollm2:huge', true).length, 0);
});

test('description search results are not discarded by a second name-only filter', () => {
  const entries = [{name: 'smollm2', variants: [{name: 'smollm2:135m', cpu_fits: true}]}];
  assert.equal(Catalogue.visible(entries, 'compact cpu', false).length, 1);
  assert.equal(Catalogue.visible(entries, 'compact cpu', true).length, 1);
});