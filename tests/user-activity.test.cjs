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
    {hour:0,input:10,output:16,requests:2,failed:1,queue:0}, {hour:3600,input:0,output:0,requests:0,failed:0,queue:0}, {hour:7200,input:1,output:2,requests:0,failed:0,queue:0}]);
  assert.equal(run("UserActivity.aggregate(data, 'alice')")[0].input, 3);
  assert.equal(run("UserActivity.aggregate(data, 'bob')")[2].output, 0);
  assert.equal(run("UserActivity.aggregate(data, 'alice')")[0].failed, 1);
});

test('combined chart draws tokens, requests, red failures and queue peaks on two scales', () => {
  const colors = [], points = [], nodes = new Map();
  const ctx = {clearRect() {}, fillText() {}, beginPath() {}, moveTo(x, y) { points.push({color:this.strokeStyle,x,y}); }, lineTo() {}, stroke() { colors.push(this.strokeStyle); }};
  nodes.set('token-chart', {width:1100, height:260, getContext: () => ctx});
  const context = vm.createContext({document: {addEventListener() {}, getElementById: id => nodes.get(id)}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/user-activity.js'), 'utf8'), context);
  vm.runInContext("UserActivity.plot('token-chart', [{hour:0,input:1000,output:500,requests:4,failed:2,queue:3}], [['input','#9cdd9b','tokens'],['output','#e7ad68','tokens'],['requests','#7dcfff','counts'],['failed','#ff5959','counts'],['queue','#c69cff','counts']])", context);
  assert.deepEqual(colors.slice(-5), ['#9cdd9b', '#e7ad68', '#7dcfff', '#ff5959', '#c69cff']);
  const tokenPeak = points.find(point => point.color === '#9cdd9b');
  const requestPeak = points.find(point => point.color === '#7dcfff');
  assert.equal(tokenPeak.y, requestPeak.y, 'each axis scales independently to its own maximum');
});

test('queue aggregation selects recorded simultaneous peaks rather than summing user maxima', () => {
  const context = vm.createContext({document: {addEventListener() {}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/llameros/static/user-activity.js'), 'utf8'), context);
  vm.runInContext("data = {start:0,end:0,rows:[],queues:[{hour:0,username:'',max_size:4},{hour:0,username:'alice',max_size:3},{hour:0,username:'bob',max_size:2}]}", context);
  assert.equal(vm.runInContext('UserActivity.aggregate(data)[0].queue', context), 4);
  assert.equal(vm.runInContext("UserActivity.aggregate(data,'alice')[0].queue", context), 3);
  vm.runInContext("data.queue_username='alice';data.queues=data.queues.filter(row=>row.username==='alice')", context);
  assert.equal(vm.runInContext('UserActivity.aggregate(data)[0].queue', context), 3);
});