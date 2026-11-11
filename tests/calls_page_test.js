// SPDX-License-Identifier: GPL-3.0-or-later
// Execute the actual page script in a small DOM fixture. No browser or sockets.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map(), jobs = [];
let focused;
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.attrs = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ''; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attrs[key] = value; }
  focus() { focused = this; }
}
const get = id => {
  if (!nodes.has(id)) nodes.set(id, new Element('div'));
  return nodes.get(id);
};
const context = {
  document: {getElementById: get, createElement: tag => new Element(tag),
    querySelector: () => ({content: 'fixture-token'})},
  fetch: (path, options) => new Promise((resolve, reject) => jobs.push({path, options, resolve, reject})),
};
vm.createContext(context);
vm.runInContext(source, context);
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const respond = (job, snapshot, ok = true) => job.resolve({ok, json: () => Promise.resolve(snapshot)});
const rows = [
  {number: '+15550101', name: '<img src=x>', direction: 'missed', start_time: '2026-11-11T12:30:00Z', duration: 0, practice: true},
  {number: '+15550102', name: 'Ana', direction: 'outgoing', start_time: '2026-11-11T12:00:00Z', duration: 65.9, practice: false},
  {number: '+15550103', name: '', direction: 'incoming', start_time: '2026-11-10T12:00:00Z', duration: null, practice: true},
];
(async () => {
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].path, '/api/calls');
  assert.equal(jobs[0].options.headers['X-OPOS-Token'], 'fixture-token');
  respond(jobs.shift(), {calls: rows, missed_count: 1, recovered: 'Started empty.'});
  await flush();
  const children = get('rows').children;
  assert.equal(children.length, 5); // two headings, three anchors, in received order
  assert.equal(children[0].children[0].tag, 'h2');
  assert.equal(children[0].children[0].textContent, '2026-11-11');
  assert.equal(children[3].children[0].textContent, '2026-11-10');
  const first = children[1].children[0], second = children[2].children[0];
  assert.equal(first.tag, 'a');
  assert.equal(first.attrs.href, '/?practice=phone&n=%2B15550101');
  assert.match(first.attrs['aria-label'], /^Call back: <img src=x>.*Missed.*Practice$/);
  assert.match(second.textContent, /1 min 5 sec · Real$/);
  assert.match(children[4].children[0].textContent, /Duration unavailable · Practice$/);
  assert.equal(first.children.length, 0); // untrusted name stays text
  assert.equal(get('missed').textContent, 'Missed calls in this log: 1');
  assert.equal(get('recovered').textContent, 'Started empty.');
  get('find').value = 'ana'; get('find').oninput();
  assert.equal(get('rows').children.length, 2);
  assert.match(get('rows').children[1].children[0].textContent, /^Ana/);
  get('find').value = '50103'; get('find').oninput();
  assert.equal(get('rows').children[0].children[0].textContent, '2026-11-10');
  get('find').value = 'absent'; get('find').oninput();
  assert.equal(get('empty').hidden, false);
  assert.equal(get('empty').textContent, 'No matching calls.');
  get('find').value = ''; get('find').oninput();
  get('clear').onclick();
  assert.equal(get('confirm').hidden, false);
  assert.equal(focused, get('clear-yes'));
  assert.equal(jobs.length, 0); // first tap does not clear
  get('clear-no').onclick();
  assert.equal(get('confirm').hidden, true);
  assert.equal(focused, get('clear'));
  get('clear').onclick(); get('clear-yes').onclick();
  assert.equal(get('clear-yes').disabled, true);
  get('clear-yes').onclick();
  assert.equal(jobs.length, 1); // no double submit
  let job = jobs.shift();
  assert.equal(job.options.method, 'POST');
  assert.deepEqual(JSON.parse(job.options.body), {action: 'clear', confirm: true});
  respond(job, {error: 'disk error'}, false); await flush();
  assert.equal(get('status').textContent, 'The call log could not be updated.');
  assert.equal(get('rows').children.length, 5);
  assert.equal(get('confirm').hidden, false);
  assert.equal(get('clear-yes').disabled, false);
  // A GET finishing after a confirmed clear must not resurrect its old rows.
  get('refresh').onclick(); const stale = jobs.shift();
  get('clear-yes').onclick(); job = jobs.shift();
  respond(job, {calls: [], missed_count: 0}); await flush();
  respond(stale, {calls: rows, missed_count: 1}); await flush();
  assert.equal(get('rows').children.length, 0);
  assert.equal(get('empty').textContent, 'No calls recorded.');
  assert.equal(get('clear').disabled, true);
  assert.equal(get('confirm').hidden, true);
  assert.equal(get('status').textContent, 'Call log cleared.');
  get('refresh').onclick(); jobs.shift().reject(new Error('offline')); await flush();
  assert.equal(get('status').textContent, 'The call log could not be updated.');
  console.log('call log script checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
