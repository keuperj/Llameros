'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
test('hourly activity aggregates all users, filters individuals and fills empty hours', () => {
  const context = vm.createContext({document: {addEventListener() {}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/user-activity.js'), 'utf8'), context);
  vm.runInContext(`data = {start:0, end:7200, rows:[
    {hour:0, username:'alice', input_tokens:3, output_tokens:5, requests:2, failed:1},
    {hour:0, username:'bob', input_tokens:7, output_tokens:11},
    {hour:7200, username:'alice', input_tokens:1, output_tokens:2}]}`, context);
  const run = expr => JSON.parse(vm.runInContext(`JSON.stringify(${expr})`, context));
  assert.deepEqual(run('UserActivity.aggregate(data)'), [
    {hour:0,input:10,output:16,requests:2,failed:1}, {hour:3600,input:0,output:0,requests:0,failed:0}, {hour:7200,input:1,output:2,requests:0,failed:0}]);
  assert.equal(run("UserActivity.aggregate(data, 'alice')")[0].input, 3);
  assert.equal(run("UserActivity.aggregate(data, 'bob')")[2].output, 0);
  assert.equal(run("UserActivity.aggregate(data, 'alice')")[0].failed, 1);
});

test('request chart draws total and failed series with a red failure stroke', () => {
  const colors = [], nodes = new Map();
  const ctx = {clearRect() {}, fillText() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() { colors.push(this.strokeStyle); }};
  nodes.set('request-chart', {width:1100, height:260, getContext: () => ctx});
  const context = vm.createContext({document: {addEventListener() {}, getElementById: id => nodes.get(id)}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/user-activity.js'), 'utf8'), context);
  vm.runInContext("UserActivity.plot('request-chart', [{hour:0, requests:4, failed:2}], [['requests','#7dcfff'],['failed','#ff5959']])", context);
  assert.deepEqual(colors.slice(-2), ['#7dcfff', '#ff5959']);
});