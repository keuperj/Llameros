'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const node = () => ({listeners:{}, attrs:{}, style:{}, hidden:true,
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setAttribute(key, value) { this.attrs[key] = value; },
    getBoundingClientRect() { return {left:490, top:300, bottom:324, width:300, height:150}; },
    contains(target) { return target === this; }});
  const tips = [0, 1].map(() => {
    const tip = node(); tip.trigger = node(); tip.content = node();
    tip.querySelector = selector => selector === '.help-trigger' ? tip.trigger : tip.content;
    tip.contains = target => [tip, tip.trigger, tip.content].includes(target);
    return tip;
  });
  const document = node(); document.activeElement = null;
  document.querySelectorAll = () => tips;
  const window = node(); window.innerWidth = 600; window.innerHeight = 400;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/tooltips.js'), 'utf8'), {document, window});
  document.listeners.DOMContentLoaded();
  return {tips, document, window};
}

test('help opens on hover, stays hoverable, fits viewport and closes on leave', () => {
  const {tips:[tip]} = setup();
  tip.listeners.mouseenter();
  assert.equal(tip.content.hidden, false);
  assert.equal(tip.trigger.attrs['aria-expanded'], 'true');
  assert.equal(tip.content.style.left, '292px');
  assert.equal(tip.content.style.bottom, '100px');
  tip.listeners.mouseleave();
  assert.equal(tip.content.hidden, true);
});

test('keyboard focus opens help; Escape dismisses without reopening until another interaction', () => {
  const {tips:[tip], document} = setup();
  document.activeElement = tip.trigger;
  tip.trigger.listeners.focus();
  tip.listeners.mouseleave();
  assert.equal(tip.content.hidden, false);
  document.listeners.keydown({key:'Escape'});
  assert.equal(tip.content.hidden, true);
  assert.equal(tip.trigger.attrs['aria-expanded'], 'false');
  tip.trigger.listeners.click({detail:0});
  assert.equal(tip.content.hidden, false);
  tip.trigger.listeners.blur();
  assert.equal(tip.content.hidden, true);
});

test('touch toggles help; outside press, scrolling, resize and another tooltip dismiss it', () => {
  const {tips:[first, second], document, window} = setup();
  first.trigger.listeners.pointerdown();
  first.trigger.listeners.focus();
  first.trigger.listeners.click({detail:1});
  assert.equal(first.content.hidden, false);
  first.trigger.listeners.pointerdown();
  first.trigger.listeners.click({detail:1});
  assert.equal(first.content.hidden, true);
  first.listeners.mouseenter(); second.listeners.mouseenter();
  assert.equal(first.content.hidden, true);
  assert.equal(second.content.hidden, false);
  document.listeners.pointerdown({target:{}});
  assert.equal(second.content.hidden, true);
  first.listeners.mouseenter();
  document.listeners.scroll({target:first.content});
  assert.equal(first.content.hidden, false);
  document.listeners.scroll({target:document});
  assert.equal(first.content.hidden, true);
  first.listeners.mouseenter(); window.listeners.resize();
  assert.equal(first.content.hidden, true);
});