// SPDX-License-Identifier: GPL-3.0-or-later
// panes.js under node's vm with a fake DOM: the Hand toggle, its label, persistence (localStorage['jaryo_hand']), html[data-hand],
// the two-pane switch html[data-panes], and fail-open when storage throws. Run: node tests/panes_dom_test.js src/opos/panes_static/panes.js
'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const SRC = fs.readFileSync(process.argv[2], 'utf8').replace('@MODES@', '1');
function world({store = {}, path = '/', ff = null, throwing = false, theme = true, loading = false} = {}) {
  const listeners = {}, attrs = {}, ds = {}; if (ff) ds.ff = ff;
  const observers = [];
  const ls = throwing ? {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }} :
    {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; }};
  const els = {};
  function el(tag) { const e = {tag, id: '', type: '', textContent: '', onclick: null, parent: null, after: null,
    insertAdjacentElement(where, n) { assert.strictEqual(where, 'afterend'); n.parent = e.parent; e.after = n; return n; }}; return e; }
  const html = {dataset: new Proxy(ds, {deleteProperty(t, k) { delete t[k]; if (k === 'ff') observers.forEach(o => o.cb()); return true; }, set(t, k, v) { t[k] = v; if (k === 'ff') observers.forEach(o => o.cb()); return true; }})};
  const doc = {readyState: loading ? 'loading' : 'complete', documentElement: html,
    getElementById: i => els[i] || null, createElement: el,
    addEventListener: (t, f) => { (listeners['doc:' + t] = listeners['doc:' + t] || []).push(f); }};
  if (theme) { els['jaryo-theme'] = el('button'); els['jaryo-theme'].id = 'jaryo-theme'; }
  const win = {addEventListener: (t, f) => { (listeners['win:' + t] = listeners['win:' + t] || []).push(f); }};
  const ctx = vm.createContext({document: doc, window: win, location: {pathname: path}, localStorage: ls, console,
    MutationObserver: class { constructor(cb) { this.cb = cb; } observe() { observers.push(this); } }});
  // make createElement register by id when the script assigns one and inserts it
  const origCreate = doc.createElement; doc.createElement = t => { const e = origCreate(t); Object.defineProperty(e, 'id', {get() { return this._id || ''; }, set(v) { this._id = v; els[v] = e; }}); return e; };
  vm.runInContext(SRC, ctx);
  const fire = (k, ev) => (listeners[k] || []).forEach(f => f(ev));
  return {html, ds, els, store, fire, doc};
}
let n = 0; const ok = (c, m) => { assert.ok(c, m); n++; };
// 1. default: Right, no key written until the owner taps
let w = world();
ok(w.ds.hand === 'right', 'default hand is right');
ok(w.els['jaryo-hand'] && w.els['jaryo-hand'].textContent === 'Hand: Right', 'button label Hand: Right');
ok(w.els['jaryo-theme'].after === w.els['jaryo-hand'], 'button sits right after #jaryo-theme');
ok(w.els['jaryo-hand'].type === 'button', 'type=button');
ok(!('jaryo_hand' in w.store), 'nothing stored before a tap');
// 2. tap toggles, persists, relabels
w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'left' && w.store.jaryo_hand === 'left' && w.els['jaryo-hand'].textContent === 'Hand: Left', 'tap -> Left, stored');
w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'right' && w.store.jaryo_hand === 'right' && w.els['jaryo-hand'].textContent === 'Hand: Right', 'tap -> Right, stored');
// 3. persisted value is read on load (and set before the DOM exists)
w = world({store: {jaryo_hand: 'left'}, loading: true});
ok(w.ds.hand === 'left', 'data-hand set in the head, before DOMContentLoaded');
ok(!w.els['jaryo-hand'], 'button not mounted until the DOM is ready');
w.fire('doc:DOMContentLoaded');
ok(w.els['jaryo-hand'].textContent === 'Hand: Left', 'label follows the stored value after DOMContentLoaded');
// 4. garbage in storage means Right
for (const bad of ['', 'LEFT', '1', 'both', 'null']) { w = world({store: {jaryo_hand: bad}}); ok(w.ds.hand === 'right', 'garbage ' + JSON.stringify(bad) + ' -> right'); }
// 5. storage that throws: still right, tap still flips the page for this visit, nothing throws
w = world({throwing: true});
ok(w.ds.hand === 'right', 'throwing storage -> right');
w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'right' || w.ds.hand === 'left', 'tap does not throw');
// 6. another tab changes the key
w = world(); w.store.jaryo_hand = 'left'; w.fire('win:storage', {key: 'jaryo_hand'});
ok(w.ds.hand === 'left' && w.els['jaryo-hand'].textContent === 'Hand: Left', 'storage event re-reads');
w.fire('win:storage', {key: 'other'}); ok(w.ds.hand === 'left', 'other keys ignored');
// 7. two-pane switch: app page needs Foldable, OS pages do not, unknown paths never
w = world({path: '/', ff: 'foldable'}); ok(w.ds.panes === 'two', 'app + foldable -> two');
w = world({path: '/'}); ok(w.ds.panes === undefined, 'app without foldable -> off');
w.ds.ff = 'foldable'; w.html.dataset.ff = 'foldable'; ok(w.ds.panes === 'two', 'foldable arriving later switches it on');
delete w.html.dataset.ff; ok(w.ds.panes === undefined, 'foldable going away switches it off');
for (const p of ['/network', '/firstrun', '/timezone']) { w = world({path: p}); ok(w.ds.panes === 'two', p + ' -> two'); }
for (const p of ['/monitor', '/update', '/osk.js']) { w = world({path: p, ff: 'foldable'}); ok(w.ds.panes === undefined, p + ' -> never'); }
// 8. no #jaryo-theme (an OS page): no button, no throw
w = world({path: '/network', theme: false}); ok(!w.els['jaryo-hand'], 'no button without the Display section');
// 9. mount once
w = world(); w.fire('doc:DOMContentLoaded'); ok(Object.keys(w.els).filter(k => k === 'jaryo-hand').length === 1, 'one button');
console.log('panes_dom_test: ' + n + ' assertions passed');
