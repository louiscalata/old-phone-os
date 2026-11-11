// SPDX-License-Identifier: GPL-3.0-or-later
// panes.js under node's vm with a fake DOM: the Hand toggle, its label, persistence (localStorage['jaryo_hand']), html[data-hand],
// the two-pane switch html[data-panes], and fail-open when storage throws. Run: node tests/panes_dom_test.js src/opos/panes_static/panes.js
'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const SRC = fs.readFileSync(process.argv[2], 'utf8').replace('@MODES@', '1');
function world({store = {}, path = '/', ff = null, throwing = false, writeThrows = false, theme = true, loading = false, home = false} = {}) {
  const listeners = {}, attrs = {}, ds = {}; if (ff) ds.ff = ff;
  const observers = [];
  const ls = throwing ? {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }} :
    {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { if (writeThrows) throw new Error('quota'); store[k] = String(v); }, removeItem: k => { delete store[k]; }};
  const els = {};
  function el(tag) { const children = [];
    const e = {tag, type: '', textContent: '', onclick: null, parent: null, after: null,
      attributes: {}, dataset: {}, _listeners: {}, children,
      get firstChild() { return children[0] || null; },
      get id() { return this._id || ''; },
      set id(v) { this._id = v; els[v] = e; },
      setAttribute(n, v) { this.attributes[n] = String(v); },
      getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attributes, n) ? this.attributes[n] : (n.indexOf('data-')===0 && this.dataset[n.slice(5)]!=null ? this.dataset[n.slice(5)] : null); },
      insertAdjacentElement(where, n) { assert.strictEqual(where, 'afterend'); n.parent = e.parent; e.after = n; return n; },
      insertBefore(n, ref) { n.parent = e; if (!ref) children.unshift(n); else { const i = children.indexOf(ref); if (i < 0) children.unshift(n); else children.splice(i, 0, n); } return n; },
      appendChild(n) { n.parent = e; children.push(n); return n; },
      remove() { if (this._id) delete els[this._id]; if (this.parent && this.parent.children) { const i = this.parent.children.indexOf(this); if (i >= 0) this.parent.children.splice(i, 1); } this.parent = null; },
      addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); },
      querySelector(sel) { if (sel === 'button') return children.find(c => /^button$/i.test(c.tag)) || null; return null; },
      contains(n) { if (n === e) return true; return children.some(c => c === n || (c.contains && c.contains(n))); },
      closest(sel) { let n = e; while (n) { if (sel === 'button' && n.tag && /^button$/i.test(n.tag)) return n; n = n.parent; } return null; }
    }; return e; }
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
  if (home) {
    const phone = el('div'); phone.id = 'phone';
    const hm = el('div'); hm.id = 'home';
    phone.appendChild(hm);
    [['phone','Phone'],['messages','Messages'],['helper','Helper'],['settings','Settings'],['allapps','All apps']].forEach(function(row){
      const b = el('button'); b.setAttribute('data-open', row[0]); b.dataset.open = row[0]; b.textContent = row[1]; hm.appendChild(b);
    });
  }
  vm.runInContext(SRC, ctx);
  const fire = (k, ev) => (listeners[k] || []).forEach(f => f(ev));
  const fireEl = (node, type, target) => (node._listeners[type] || []).forEach(f => f({target: target || node}));
  return {html, ds, els, store, fire, fireEl, doc};
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
// 5. storage that throws: still right, nothing throws, and the button follows the PAGE, not the storage: every tap flips the page and
// the label says what the page is (it used to read the hand back from storage, so it stayed "Hand: Right" and every tap set Left)
w = world({throwing: true});
ok(w.ds.hand === 'right', 'throwing storage -> right');
ok(w.els['jaryo-hand'].textContent === 'Hand: Right', 'label before any tap');
const flips = [];
for (let i = 0; i < 4; i++) { w.els['jaryo-hand'].onclick(); flips.push(w.ds.hand + '/' + w.els['jaryo-hand'].textContent); }
ok(flips.join() === 'left/Hand: Left,right/Hand: Right,left/Hand: Left,right/Hand: Right', 'throwing storage: four taps flip the page and the label together: ' + flips.join());
// the same when only the write fails (a full or read-only store): a read finds the old value, the page still goes by itself
w = world({store: {jaryo_hand: 'left'}, writeThrows: true});
ok(w.ds.hand === 'left' && w.els['jaryo-hand'].textContent === 'Hand: Left', 'read works, write fails: starts Left');
w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'right' && w.els['jaryo-hand'].textContent === 'Hand: Right' && w.store.jaryo_hand === 'left', 'write fails: the page flips to Right, the label agrees');
w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'left' && w.els['jaryo-hand'].textContent === 'Hand: Left', 'and back to Left');
// a page whose hand was set by something else (another script, a storage event) is flipped from what it is
w = world(); w.html.dataset.hand = 'left'; w.els['jaryo-hand'].onclick();
ok(w.ds.hand === 'right' && w.els['jaryo-hand'].textContent === 'Hand: Right', 'a tap flips the hand the page has');
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
// 10. span detail is filled again after leaving two-pane mode and coming back, and a later tile selection writes to the live node
w = world({path: '/', ff: 'foldable', home: true});
ok(w.ds.panes === 'two', 'home + foldable starts two-pane');
ok(w.els['pane-detail'] && /Phone/.test(w.els['pane-detail'].textContent), 'detail names the first tile');
const firstDetail = w.els['pane-detail'];
delete w.html.dataset.ff;
ok(w.ds.panes === undefined, 'leaving foldable drops two-pane');
ok(!w.els['pane-detail'], 'pane-detail is removed on one pane');
w.html.dataset.ff = 'foldable';
ok(w.ds.panes === 'two', 'foldable returning restores two-pane');
ok(w.els['pane-detail'] && w.els['pane-detail'] !== firstDetail, 'a new pane-detail node is created');
ok(/Phone/.test(w.els['pane-detail'].textContent), 're-entry fills the new node, not a blank');
const msgs = w.els.home.children.find(c => c.dataset && c.dataset.open === 'messages');
w.fireEl(w.els.home, 'focusin', msgs);
ok(/Messages/.test(w.els['pane-detail'].textContent) && /Practice messages/.test(w.els['pane-detail'].textContent), 'later tile selection updates the live node');
console.log('panes_dom_test: ' + n + ' assertions passed');
