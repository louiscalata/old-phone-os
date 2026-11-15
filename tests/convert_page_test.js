// SPDX-License-Identifier: GPL-3.0-or-later
// Execute the actual converter page script in a small DOM fixture. No browser or sockets.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map(), jobs = [];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.attrs = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ''; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attrs[key] = value; }
}
const get = id => {
  if (!nodes.has(id)) nodes.set(id, new Element('div'));
  return nodes.get(id);
};
get('amount').value = '1';
const context = {
  document: {getElementById: get, createElement: tag => new Element(tag),
    querySelector: () => ({content: 'fixture-token'})},
  fetch: (path, options) => new Promise((resolve, reject) => jobs.push({path, options, resolve, reject})),
};
vm.createContext(context);
vm.runInContext(source, context);
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const respond = (job, body, status = 200) => job.resolve({status, json: () => Promise.resolve(body)});
const kinds = [
  {id: 'length', title: 'Length', src: 'km', dst: 'mi', units: [{id: 'km', title: 'km'}, {id: 'mi', title: 'mile'}]},
  {id: 'temperature', title: 'Temperature', src: 'c', dst: 'f', units: [{id: 'c', title: 'C'}, {id: 'f', title: 'F'}]},
];
(async () => {
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].path, '/api/convert');
  assert.equal(jobs[0].options.method, 'GET');
  assert.equal(jobs[0].options.headers['X-OPOS-Token'], 'fixture-token');
  respond(jobs.shift(), {ok: true, kinds});
  await flush();
  assert.equal(get('kinds').children.length, 2);
  assert.equal(get('from').children[0].attrs['aria-pressed'], 'true');
  assert.equal(jobs.length, 1, 'the first conversion is asked for');
  const first = jobs.shift();
  assert.deepEqual(JSON.parse(first.options.body), {kind: 'length', value: '1', src: 'km', dst: 'mi'});
  // Two keystrokes: the reply to the newer one arrives first, the older one last. The newer result stays.
  get('amount').value = '2'; get('amount').oninput();
  get('amount').value = '3'; get('amount').oninput();
  const older = jobs.shift(), newer = jobs.shift();
  respond(newer, {ok: true, text: '1.86411', line: '3 km = 1.86411 mile'});
  await flush();
  respond(older, {ok: true, text: '1.24274', line: '2 km = 1.24274 mile'});
  respond(first, {ok: true, text: '0.621371', line: '1 km = 0.621371 mile'});
  await flush();
  assert.equal(get('result').textContent, '1.86411');
  assert.equal(get('line').textContent, '3 km = 1.86411 mile');
  // A refusal shows its plain words and clears the line; a later good answer clears the words.
  get('amount').value = '-1'; get('amount').oninput();
  respond(jobs.shift(), {error: 'sign'}, 400);
  await flush();
  assert.equal(get('err').textContent, 'Length, mass and volume stay at zero or more.');
  assert.equal(get('line').textContent, '');
  // Choosing a measure takes its default units.
  get('kinds').children[1].onclick();
  const temp = jobs.shift();
  assert.deepEqual(JSON.parse(temp.options.body), {kind: 'temperature', value: '-1', src: 'c', dst: 'f'});
  respond(temp, {ok: true, text: '30.2', line: '-1 C = 30.2 F'});
  await flush();
  assert.equal(get('err').textContent, '');
  assert.equal(get('result').textContent, '30.2');
  // A failed request says so instead of leaving an old result unexplained.
  get('amount').value = '5'; get('amount').oninput();
  jobs.shift().reject(new Error('offline'));
  await flush();
  assert.equal(get('err').textContent, 'That conversion is not available.');
  console.log('converter script checks passed');
})().catch(error => { console.error(error); process.exit(1); });
