// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise token rotation and the public-only controls without a browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/opos/public_static/public-demo.js', 'utf8');
const elements = {
  token: {content: 'old'},
  'public-session': {textContent: ''},
  'glimmer-credit': {textContent: 'waiting'},
  'helper-where': {textContent: 'Answers are made on this phone when possible.'},
  webq: {checked: true, disabled: false},
  helperrestart: {disabled: false}
};
let calls = 0, rendered = [], ready, mutation, blurred = 0, scrolled = 0;
let response;
const scope = {
  window: {fetch: async () => {calls++; return response;}, location: {hash: ''},
    scrollTo: options => {assert.equal(options.top, 0); scrolled++;}},
  document: {
    activeElement: {id: 'unlock', blur: () => {blurred++;}},
    querySelector: () => elements.token,
    getElementById: id => elements[id],
    addEventListener: (event, callback) => {assert.equal(event, 'DOMContentLoaded'); ready = callback;}
  },
  MutationObserver: class {constructor(callback) {mutation = callback;} observe() {}},
  render: async state => {rendered.push(state);}
};
vm.runInNewContext(source, scope);
(async () => {
  ready();
  assert.equal(blurred, 1);
  assert.equal(scrolled, 1);
  scope.window.location.hash = '#try-phone';
  ready();
  assert.equal(scrolled, 1, 'explicit phone navigation must not scroll back');
  assert.equal(elements.webq.checked, false);
  assert.equal(elements.webq.disabled, true);
  assert.equal(elements.helperrestart.disabled, true);
  assert.match(elements['glimmer-credit'].textContent, /answers come from the rules/);
  const helperNotice = 'Demo phone. These answers come from the battery rules. Muse Glimmer runs only on your own computer.';
  assert.equal(elements['helper-where'].textContent, helperNotice);
  elements['glimmer-credit'].textContent = 'no reply';
  elements['helper-where'].textContent = 'Answers are made on this phone when possible.';
  mutation();
  assert.match(elements['glimmer-credit'].textContent, /answers come from the rules/);
  assert.equal(elements['helper-where'].textContent, helperNotice);
  response = new Response(JSON.stringify({battery: {percent: 60}, threads: []}), {
    headers: {'X-OPOS-Token': 'fresh', 'X-OPOS-Session-Reset': '1'}
  });
  const reply = await scope.window.fetch('/api', {method: 'POST', body: '{"action":"send"}'});
  assert.equal(calls, 1, 'must never replay a stale mutation');
  assert.equal(elements.token.content, 'fresh');
  assert.equal(rendered.length, 1);
  assert.equal(rendered[0].battery.percent, 60);
  assert.match(elements['public-session'].textContent, /fresh demo phone/);
  assert.deepEqual((await reply.json()).threads, [], 'caller can still consume the response body');
  response = new Response('{}', {headers: {'X-OPOS-Token': 'foreign'}});
  await scope.window.fetch('/not-api');
  assert.equal(elements.token.content, 'fresh', 'other routes cannot rotate tokens');
  response = new Response('{"error":"forbidden"}', {status: 403});
  await scope.window.fetch('/api');
  assert.equal(calls, 3);
  assert.equal(elements.token.content, 'fresh');
  console.log('PASS: token rotation, no replay, readable response, rules notice and disabled controls');
})().catch(error => {console.error(error); process.exitCode = 1;});
