'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Catalogue = require('../src/llameros/static/catalogue.js');

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