// SPDX-License-Identifier: GPL-3.0-or-later
// Execute the shipped script in a Node VM. No browser or server is started here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const label = {firstChild: {textContent: ''}};
const elements = {};
const el = id => elements[id] || (elements[id] = {
  id, value: '', textContent: '', style: {}, dataset: {}, listeners: {},
  addEventListener(type, fn) {(this.listeners[type] ||= []).push(fn);},
  setAttribute(key, value) {this[key] = value;},
});
const slider = Object.assign(el('vol'), {value: '95', closest: () => label});
const output = el('vol-val'), fill = el('meter-fill'), meter = el('meter');
el('topbar').value = 'v';
el('bright').value = '100';
el('sb-time').textContent = '12:00';
const observers = [];
let renders = 0;
const context = {
  document: {getElementById: el, documentElement: {style: {}}},
  G: el, barM: undefined, meterEl: meter,
  localStorage: {getItem() {return null;}, setItem() {throw Error('no persistence expected');}},
  MutationObserver: class {constructor(fn) {observers.push(fn);} observe() {}},
  render: async () => {renders++;},
};
vm.createContext(context);
const base = fs.readFileSync(process.argv[3], 'utf8');
vm.runInContext(base.slice(base.indexOf('function barPaint()'), base.indexOf('async function api(')), context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
(async () => {
  assert.equal(slider.disabled, true);
  assert.match(label.firstChild.textContent, /simulated; sound is not changed/);
  assert.equal(output.textContent, 'Unavailable');
  for (const percent of [0, 55, 100]) {
    await context.render({volume_keys: {simulated: true, percent}});
    assert.equal(slider.value, percent);
    assert.equal(output.textContent, percent + '%');
    assert.equal(fill.style.width, percent + '%');
  }
  for (const state of [{}, null, {error: 'offline'},
       {volume_keys: {simulated: false, percent: 60}},
       ...[-1, 101, null, true, '60'].map(percent => ({volume_keys: {simulated: true, percent}}))]) {
    await context.render(state);
    // Exercise callbacks captured before volume.js loaded, as well as direct paints.
    for (const id of ['topbar', 'bright', 'night']) {
      for (const fn of el(id).listeners.input) fn();
      assert.equal(output.textContent, 'Unavailable', id + ' input');
    }
    for (const fn of observers) fn();
    context.barPaint();
    assert.equal(output.textContent, 'Unavailable');
    assert.equal(fill.style.width, '0%');
    assert.match(meter['aria-label'], /Simulated volume unavailable/);
  }
  await context.render({volume_keys: {simulated: true, percent: 65}});
  assert.equal(output.textContent, '65%');
  assert.equal(renders, 13);
  console.log('PASS simulated volume UI: refresh, bounds, unavailable, recovery');
})().catch(error => {console.error(error); process.exitCode = 1;});
