// SPDX-License-Identifier: GPL-3.0-or-later
// Logic of the Duo OS on-screen keyboard (src/opos/osk_static/osk.js) under node, with a fake field in place of the page.
// Run by tests/test_osk.py; also: node tests/osk_core_test.js
'use strict';
const assert = require('assert');
const path = require('path');
const core = require(path.join(__dirname, '..', 'src', 'opos', 'osk_static', 'osk.js'));
const M = core.M, st = core.st;
let n = 0;
function t(name, fn) { try { fn(); n++; } catch (e) { console.error('FAIL ' + name + '\n' + (e && e.stack || e)); process.exitCode = 1; } }

// A field: value, selection, maxlength and an event log; listeners may cancel an event like a page handler would.
function field(o) {
  o = o || {};
  const f = {
    tagName: o.tag || 'INPUT', type: o.type || 'text', value: o.value || '', maxLength: o.max || -1, form: o.form || null,
    attrs: o.attrs || {}, events: [], listeners: {},
    selectionStart: o.start === undefined ? (o.value || '').length : o.start,
    selectionEnd: o.end === undefined ? (o.value || '').length : o.end,
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setSelectionRange(a, b) { if (this.noSel) throw new Error('InvalidStateError'); this.selectionStart = a; this.selectionEnd = b; },
    on(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    dispatchEvent(e) {
      this.events.push({type: e.type, key: e.key, inputType: e.inputType, data: e.data, value: this.value});
      for (const fn of this.listeners[e.type] || []) fn(e, this);
      return !e.defaultPrevented;
    }
  };
  if (o.noSel) {
    Object.defineProperty(f, 'selectionStart', {get() { return null; }});
    Object.defineProperty(f, 'selectionEnd', {get() { return null; }});
    f.noSel = true;
  }
  if (o.reject) { // type=number: a value the field cannot hold reads back as ''
    let v = f.value;
    Object.defineProperty(f, 'value', {get() { return v; }, set(x) { v = /^[0-9]*$/.test(x) ? x : ''; }});
  }
  return f;
}
core.setEventFactory((type, init) => {
  const e = Object.assign({type, defaultPrevented: false, preventDefault() { if (this.cancelable) this.defaultPrevented = true; }}, init);
  return e;
});
function use(f) { st.shift = 0; st.split = true; core.setField(f); return f; }
function keys(f, s) { for (const ch of s) core.act({k: ch === ' ' ? 'space' : 'ch', v: ch, w: 1}); return f.value; }
const types = f => f.events.map(e => e.type);

t('kindFor picks the keyboard from the field', () => {
  assert.strictEqual(core.kindFor('input', 'text', ''), 'alpha');
  assert.strictEqual(core.kindFor('INPUT', 'search', ''), 'alpha');
  assert.strictEqual(core.kindFor('INPUT', 'password', ''), 'alpha');
  assert.strictEqual(core.kindFor('INPUT', undefined, ''), 'alpha');
  assert.strictEqual(core.kindFor('TEXTAREA', 'textarea', ''), 'alpha');
  assert.strictEqual(core.kindFor('INPUT', 'tel', ''), 'tel');
  assert.strictEqual(core.kindFor('INPUT', 'text', 'tel'), 'tel');
  assert.strictEqual(core.kindFor('INPUT', 'number', ''), 'int');
  assert.strictEqual(core.kindFor('INPUT', 'text', 'numeric'), 'int');
  assert.strictEqual(core.kindFor('INPUT', 'text', 'decimal'), 'dec');
  assert.strictEqual(core.kindFor('INPUT', 'time', ''), 'time');
  for (const ty of ['checkbox', 'radio', 'range', 'button', 'submit', 'file', 'color', 'hidden', 'date', 'datetime-local'])
    assert.strictEqual(core.kindFor('INPUT', ty, ''), '', ty);
  assert.strictEqual(core.kindFor('INPUT', 'text', 'none'), '', 'inputmode=none asks for no keyboard');
  assert.strictEqual(core.kindFor('SELECT', '', ''), '');
  assert.strictEqual(core.kindFor('DIV', '', ''), '');
});

t('insertAt and backspaceAt', () => {
  assert.deepStrictEqual(core.insertAt('', 0, 0, 'a', 0), {value: 'a', caret: 1});
  assert.deepStrictEqual(core.insertAt('ac', 1, 1, 'b', 0), {value: 'abc', caret: 2});
  assert.deepStrictEqual(core.insertAt('abc', 1, 2, 'X', 0), {value: 'aXc', caret: 2}, 'replaces the selection');
  assert.deepStrictEqual(core.insertAt('abc', 2, 1, 'X', 0), {value: 'aXc', caret: 2}, 'backwards selection');
  assert.strictEqual(core.insertAt('abc', 3, 3, 'd', 3), null, 'maxlength refuses');
  assert.deepStrictEqual(core.insertAt('abc', 1, 2, 'd', 3), {value: 'adc', caret: 2}, 'maxlength lets a replacement through');
  assert.deepStrictEqual(core.insertAt('ab', 2, 2, 'cd', 3), {value: 'abc', caret: 3}, 'maxlength trims');
  assert.strictEqual(core.insertAt('a', 1, 1, '', 0), null);
  assert.deepStrictEqual(core.backspaceAt('abc', 3, 3), {value: 'ab', caret: 2});
  assert.deepStrictEqual(core.backspaceAt('abc', 1, 1), {value: 'bc', caret: 0});
  assert.strictEqual(core.backspaceAt('abc', 0, 0), null);
  assert.deepStrictEqual(core.backspaceAt('abcd', 1, 3), {value: 'ad', caret: 1}, 'deletes the selection');
  assert.deepStrictEqual(core.backspaceAt('a\u{1F600}', 3, 3), {value: 'a', caret: 1}, 'a surrogate pair goes whole');
});

t('capitals and shift', () => {
  assert.strictEqual(core.autocapMode('INPUT', 'text', ''), 'sentences');
  assert.strictEqual(core.autocapMode('TEXTAREA', 'textarea', ''), 'sentences');
  for (const ty of ['password', 'email', 'url', 'search', 'tel']) assert.strictEqual(core.autocapMode('INPUT', ty, ''), 'none', ty);
  assert.strictEqual(core.autocapMode('INPUT', 'text', 'off'), 'none');
  assert.strictEqual(core.autocapMode('INPUT', 'email', 'words'), 'words');
  assert.strictEqual(core.wantsShift('', 'sentences'), true);
  assert.strictEqual(core.wantsShift('Hello', 'sentences'), false);
  assert.strictEqual(core.wantsShift('Hello. ', 'sentences'), true);
  assert.strictEqual(core.wantsShift('Hello.', 'sentences'), false);
  assert.strictEqual(core.wantsShift('Why? ', 'sentences'), true);
  assert.strictEqual(core.wantsShift('a\n', 'sentences'), true);
  assert.strictEqual(core.wantsShift('a b', 'words'), false);
  assert.strictEqual(core.wantsShift('a ', 'words'), true);
  assert.strictEqual(core.wantsShift('anything', 'none'), false);
  assert.strictEqual(core.wantsShift('', 'none'), false);
  let s = core.shiftTap(0, 1000, 0);
  assert.deepStrictEqual(s, {state: 1, last: 1000});
  assert.strictEqual(core.shiftTap(s.state, 1200, s.last).state, 2, 'double tap is caps lock');
  assert.strictEqual(core.shiftTap(1, 2000, 1000).state, 0, 'a slow second tap turns it off');
  assert.strictEqual(core.shiftTap(2, 3000, 0).state, 0, 'a tap leaves caps lock');
});

t('typing makes the browser events a keyboard would: keydown, beforeinput, input, keyup', () => {
  const f = use(field());
  core.act({k: 'ch', v: 'h'});
  assert.deepStrictEqual(types(f), ['keydown', 'beforeinput', 'input', 'keyup']);
  assert.strictEqual(f.value, 'H', 'the first letter of a sentence is a capital');
  assert.strictEqual(f.events[0].key, 'h'.toUpperCase());
  assert.strictEqual(f.events[2].inputType, 'insertText');
  assert.strictEqual(f.events[2].data, 'H');
  assert.strictEqual(f.selectionStart, 1);
  assert.strictEqual(keys(f, 'i there'), 'Hi there');
  assert.strictEqual(keys(f, '. ok'), 'Hi there. Ok', 'capital after a full stop');
});

t('shift, caps lock and a field that wants no capitals', () => {
  const f = use(field({type: 'password'}));
  assert.strictEqual(keys(f, 'ab'), 'ab');
  core.act({k: 'shift'});
  assert.strictEqual(st.shift, 1);
  keys(f, 'c');
  assert.strictEqual(f.value, 'abC');
  assert.strictEqual(st.shift, 0, 'one shot');
  core.act({k: 'shift'}); core.act({k: 'shift'});
  assert.strictEqual(st.shift, 2, 'two quick taps lock caps');
  keys(f, 'de');
  assert.strictEqual(f.value, 'abCDE');
  assert.strictEqual(st.shift, 2);
  core.act({k: 'shift'});
  assert.strictEqual(st.shift, 0);
});

t('typing in the middle keeps the caret and honours maxlength', () => {
  const f = use(field({value: 'ac', start: 1, end: 1, type: 'password'}));
  keys(f, 'b');
  assert.strictEqual(f.value, 'abc');
  assert.strictEqual(f.selectionStart, 2);
  const g = use(field({value: 'abc', max: 3, type: 'password'}));
  keys(g, 'd');
  assert.strictEqual(g.value, 'abc');
  assert.ok(!types(g).includes('input'), 'a refused key sends no input event');
  const h = use(field({type: 'email', noSel: true, value: 'a@b'}));
  keys(h, '.x');
  assert.strictEqual(h.value, 'a@b.x', 'a field without a selection API is edited at its end');
});

t('a handler may cancel a key', () => {
  const f = use(field({type: 'password'}));
  f.on('keydown', e => { if (e.key === 'q') e.preventDefault(); });
  keys(f, 'qa');
  assert.strictEqual(f.value, 'a');
  const g = use(field({type: 'password'}));
  g.on('beforeinput', e => e.preventDefault());
  keys(g, 'a');
  assert.strictEqual(g.value, '');
});

t('backspace deletes, then does nothing at the start', () => {
  const f = use(field({value: 'ab', type: 'password'}));
  core.act({k: 'bs'}); core.act({k: 'bs'}); core.act({k: 'bs'});
  assert.strictEqual(f.value, '');
  assert.strictEqual(f.events.filter(e => e.type === 'input').length, 2);
  assert.strictEqual(f.events.filter(e => e.type === 'input')[0].inputType, 'deleteContentBackward');
});

t('Enter runs the page handler and fires change once', () => {
  const f = use(field({type: 'password'}));
  let seen = 0;
  f.on('keydown', e => { if (e.key === 'Enter') { seen++; e.preventDefault(); } });
  keys(f, 'hi');
  core.act({k: 'enter'});
  assert.strictEqual(seen, 1);
  const e = f.events.map(x => x.type + (x.key ? ':' + x.key : ''));
  assert.ok(e.includes('keydown:Enter') && e.includes('keyup:Enter'));
  // The page handled Enter itself (preventDefault): the keyboard adds nothing, and change comes when the field is left.
  assert.strictEqual(f.events.filter(x => x.type === 'change').length, 0);
  core.commit(f);
  assert.strictEqual(f.events.filter(x => x.type === 'change').length, 1);
  core.commit(f);
  assert.strictEqual(f.events.filter(x => x.type === 'change').length, 1, 'no second change for the same value');
  // Without a handler Enter commits.
  const g = use(field({type: 'password'}));
  keys(g, 'x');
  core.act({k: 'enter'});
  assert.strictEqual(g.events.filter(x => x.type === 'change').length, 1);
  // A form is submitted.
  let sub = 0;
  const h = use(field({type: 'password', form: {requestSubmit() { sub++; }}}));
  core.act({k: 'enter'});
  assert.strictEqual(sub, 1);
});

t('Enter in a textarea is a new line', () => {
  const f = use(field({tag: 'TEXTAREA', type: 'textarea', value: 'a', attrs: {autocapitalize: 'off'}}));
  core.act({k: 'enter'});
  assert.strictEqual(f.value, 'a\n');
});

t('a number field takes digits and refuses what it cannot hold', () => {
  const f = use(field({type: 'number', noSel: true, reject: true}));
  assert.strictEqual(st.kind, 'int');
  keys(f, '12');
  assert.strictEqual(f.value, '12');
  keys(f, 'x');
  assert.strictEqual(f.value, '12', 'refused, and restored');
  core.act({k: 'bs'});
  assert.strictEqual(f.value, '1');
});

t('the 24-hour pad', () => {
  assert.strictEqual(core.timeAdd('', '1'), '1');
  assert.strictEqual(core.timeAdd('', '2'), '2');
  assert.strictEqual(core.timeAdd('', '3'), '03', 'a first digit above 2 is the hour 0n');
  assert.strictEqual(core.timeAdd('', '9'), '09');
  assert.strictEqual(core.timeAdd('2', '3'), '23');
  assert.strictEqual(core.timeAdd('2', '4'), null, 'no hour 24');
  assert.strictEqual(core.timeAdd('16', '6'), null, 'no minute 60');
  assert.strictEqual(core.timeAdd('16', '5'), '165');
  assert.strictEqual(core.timeAdd('1630', '1'), null, 'four digits is all');
  assert.strictEqual(core.timeAdd('1', 'x'), null);
  assert.strictEqual(core.timeShow(''), '__:__');
  assert.strictEqual(core.timeShow('1'), '1_:__');
  assert.strictEqual(core.timeShow('163'), '16:3_');
  assert.strictEqual(core.timeShow('1630'), '16:30');
  assert.strictEqual(core.timeValue('1630'), '16:30');
  assert.strictEqual(core.timeValue('16'), '16:00');
  assert.strictEqual(core.timeValue('163'), '16:30');
  assert.strictEqual(core.timeValue('093'), '09:30');
  assert.strictEqual(core.timeValue('1'), '');
  assert.strictEqual(core.timeFromValue('07:45'), '0745');
  assert.strictEqual(core.timeFromValue(''), '');
});

t('typing a time into <input type=time> sets it in one go and fires input and change', () => {
  const f = use(field({type: 'time', value: '12:00', noSel: true}));
  assert.strictEqual(st.kind, 'time');
  assert.strictEqual(st.tbuf, '1200');
  for (const d of '16') core.act({k: 'ch', v: d});
  assert.strictEqual(f.value, '12:00', 'nothing is set before four digits');
  assert.strictEqual(st.tbuf, '16', 'the first digit replaced the old time');
  core.act({k: 'ch', v: '3'});
  assert.strictEqual(f.value, '12:00');
  core.act({k: 'ch', v: '0'});
  assert.strictEqual(f.value, '16:30');
  const ev = f.events.map(e => e.type);
  assert.deepStrictEqual(ev, ['input', 'change']);
  core.act({k: 'ch', v: '9'});
  assert.strictEqual(f.value, '16:30', 'a fifth digit is ignored');
  core.act({k: 'bs'});
  assert.strictEqual(st.tbuf, '163');
  core.act({k: 'ch', v: '5'});
  assert.strictEqual(f.value, '16:35');
});

t('Done fills in what is there; Clear empties the field', () => {
  const f = use(field({type: 'time', value: '', noSel: true}));
  core.act({k: 'ch', v: '7'}); // 07
  assert.strictEqual(core.act({k: 'done'}), 'hide');
  assert.strictEqual(f.value, '07:00');
  const g = use(field({type: 'time', value: '08:15', noSel: true}));
  assert.strictEqual(core.act({k: 'clear'}), 'info');
  assert.strictEqual(g.value, '');
  assert.ok(g.events.some(e => e.type === 'change'));
  const h = use(field({type: 'time', value: '', noSel: true}));
  core.act({k: 'ch', v: '1'}); // one digit: less than an hour
  core.act({k: 'done'});
  assert.strictEqual(h.value, '', 'less than an hour changes nothing');
});

t('geometry: the browser, the page and the server each say where the hinge is', () => {
  assert.deepStrictEqual(core.geometry({vw: 1392, segs: [{x: 0, w: 675}, {x: 717, w: 675}], cssHinge: 0, cssPane: 0, cfgHinge: 42}), {pane: 675, hinge: 42});
  assert.deepStrictEqual(core.geometry({vw: 1392, segs: null, cssHinge: 40, cssPane: 676, cfgHinge: 42}), {pane: 676, hinge: 40}, 'OPOS_SPAN beats the server default');
  assert.deepStrictEqual(core.geometry({vw: 1392, segs: null, cssHinge: 0, cssPane: 0, cfgHinge: 42}), {pane: 675, hinge: 42});
  assert.deepStrictEqual(core.geometry({vw: 1392, segs: null, cssHinge: 42, cssPane: 0, cfgHinge: 0}), {pane: 675, hinge: 42});
  assert.strictEqual(core.geometry({vw: 1392, segs: null, cssHinge: 0, cssPane: 0, cfgHinge: 0}), null, 'no hinge known');
  assert.strictEqual(core.geometry({vw: 675, segs: null, cssHinge: 42, cssPane: 675, cfgHinge: 42}), null, 'a window on one pane');
  assert.strictEqual(core.geometry({vw: 390, segs: null, cssHinge: 0, cssPane: 0, cfgHinge: 42}), null, 'a phone');
  assert.ok(core.geometry({vw: 1300, segs: null, cssHinge: 42, cssPane: 675, cfgHinge: 42}), 'a little narrower than the span still spans');
});

const SPAN = {pane: 675, hinge: 42};
t('plan: a half on each pane, never on the hinge', () => {
  const p = core.plan({vw: 1392, kind: 'alpha', split: true, cx: 300, span: SPAN});
  assert.strictEqual(p.layout, 'split');
  assert.strictEqual(p.half, 420);
  assert.ok(p.left + p.half <= SPAN.pane, 'the left half ends before the hinge');
  assert.ok(1392 - M.edge - p.half >= SPAN.pane + SPAN.hinge, 'the right half starts after it');
  assert.strictEqual(p.left, M.edge);
  assert.strictEqual(p.width, 1392 - 2 * M.edge);
});

t('plan: one block on the pane that holds the field', () => {
  const l = core.plan({vw: 1392, kind: 'alpha', split: false, cx: 300, span: SPAN});
  const r = core.plan({vw: 1392, kind: 'alpha', split: false, cx: 1000, span: SPAN});
  assert.deepStrictEqual([l.layout, l.left, l.width, l.side], ['pane', 4, 667, 0]);
  assert.deepStrictEqual([r.layout, r.left, r.width, r.side], ['pane', 721, 667, 1]);
  assert.ok(l.left + l.width <= 675 && r.left >= 717);
  assert.strictEqual(core.plan({vw: 1392, kind: 'alpha', split: false, cx: 690, span: SPAN}).side, 0, 'a field over the hinge goes to the nearer pane');
  assert.strictEqual(core.plan({vw: 1392, kind: 'alpha', split: false, cx: 700, span: SPAN}).side, 1);
});

t('plan: a pad hugs the outer edge of its pane and stays one hand wide', () => {
  const l = core.plan({vw: 1392, kind: 'tel', split: true, cx: 300, span: SPAN});
  const r = core.plan({vw: 1392, kind: 'time', split: true, cx: 1000, span: SPAN});
  assert.deepStrictEqual([l.layout, l.left, l.width], ['pane', 4, 380]);
  assert.deepStrictEqual([r.layout, r.left, r.width], ['pane', 1392 - 4 - 380, 380]);
});

t('plan: a window on one pane gets one centred panel', () => {
  const p = core.plan({vw: 390, kind: 'alpha', split: true, cx: 100, span: null});
  assert.deepStrictEqual([p.layout, p.left, p.width], ['single', 4, 382]);
  const w = core.plan({vw: 1000, kind: 'alpha', split: true, cx: 100, span: null});
  assert.deepStrictEqual([w.layout, w.width, w.left], ['single', 760, 120]);
});

t('layouts: both halves weigh the same, so a split row is two rectangles', () => {
  for (const name of Object.keys(core.LAYOUTS)) core.LAYOUTS[name].forEach((row, i) => {
    assert.strictEqual(core.sumW(row.l), core.sumW(row.r), name + ' row ' + i);
    assert.strictEqual(row.l.length > 0 && row.r.length > 0, true);
  });
  assert.strictEqual(core.LAYOUTS.alpha.length, 4);
  const letters = core.LAYOUTS.alpha.slice(0, 3).map(r => r.l.concat(r.r).filter(k => k.k === 'ch').map(k => k.v).join('')).join('');
  assert.strictEqual(letters.replace(/[^a-z]/g, '').split('').sort().join(''), 'abcdefghijklmnopqrstuvwxyz', 'every letter exactly once');
});

t('layouts: every real key is at least 56 CSS px wide on the Duo, split or in one block', () => {
  const half = core.plan({vw: 1392, kind: 'alpha', split: true, cx: 300, span: SPAN}).half;
  const pane = core.plan({vw: 1392, kind: 'alpha', split: false, cx: 300, span: SPAN}).width;
  const wide = core.plan({vw: 1000, kind: 'alpha', split: true, cx: 300, span: null}).width;
  for (const name of Object.keys(core.LAYOUTS)) core.LAYOUTS[name].forEach((row, i) => {
    assert.ok(core.minKey(row.l, half) >= M.key, name + ' ' + i + ' left half ' + core.minKey(row.l, half));
    assert.ok(core.minKey(row.r, half) >= M.key, name + ' ' + i + ' right half ' + core.minKey(row.r, half));
    assert.ok(core.minKey(core.flat(row), pane) >= M.key, name + ' ' + i + ' block ' + core.minKey(core.flat(row), pane));
    assert.ok(core.minKey(core.flat(row), wide) >= M.key, name + ' ' + i + ' single ' + core.minKey(core.flat(row), wide));
  });
  for (const kind of ['tel', 'int', 'dec', 'time']) core.padRows(kind).forEach((row, i) => {
    assert.ok(core.minKey(row, M.padMax) >= M.key, kind + ' ' + i + ' ' + core.minKey(row, M.padMax));
    assert.strictEqual(core.sumW(row), 4, kind + ' row ' + i + ' is four columns');
  });
});

t('layouts: the block merges the two space bars, and the other keys are untouched', () => {
  const row = core.LAYOUTS.alpha[3], flat = core.flat(row);
  assert.strictEqual(flat.filter(k => k.k === 'space').length, 1);
  assert.strictEqual(core.sumW(flat), core.sumW(row.l) + core.sumW(row.r));
  assert.deepStrictEqual(flat.map(k => k.k), ['mode', 'ch', 'space', 'ch', 'enter']);
});

t('layouts: a way to every layout and back, a delete key on each', () => {
  const to = (name) => new Set([].concat(...core.LAYOUTS[name].map(r => r.l.concat(r.r))).filter(k => k.k === 'mode').map(k => k.to));
  assert.ok(to('alpha').has('num') && to('num').has('alpha') && to('num').has('sym') && to('sym').has('alpha') && to('sym').has('num'));
  for (const name of Object.keys(core.LAYOUTS)) {
    const all = [].concat(...core.LAYOUTS[name].map(r => r.l.concat(r.r)));
    assert.ok(all.some(k => k.k === 'bs'), name + ' has delete');
    assert.ok(all.some(k => k.k === 'enter'), name + ' has enter');
    assert.ok(all.some(k => k.k === 'space'), name + ' has space');
  }
  for (const kind of ['tel', 'int', 'dec', 'time']) {
    const all = [].concat(...core.padRows(kind));
    assert.ok(all.some(k => k.k === 'bs') && all.some(k => k.k === 'hide'), kind);
    assert.ok(all.some(k => k.k === 'enter' || k.k === 'done'), kind);
    for (let d = 0; d <= 9; d++) assert.ok(all.some(k => k.v === String(d)), kind + ' digit ' + d);
  }
});

t('needScroll keeps the field clear of the keyboard', () => {
  assert.strictEqual(core.needScroll(100, 150, 600, 36, 12), 0, 'already clear');
  assert.strictEqual(core.needScroll(580, 630, 600, 36, 12), 42, 'under the keyboard: up by what covers it plus the margin');
  assert.strictEqual(core.needScroll(10, 50, 600, 36, 12), -38, 'above the top edge');
  assert.strictEqual(core.needScroll(100, 900, 600, 36, 12), 52, 'taller than the room: its top is shown');
});

t('mode keys switch layouts and ask for a rebuild', () => {
  use(field({type: 'password'}));
  assert.strictEqual(core.act({k: 'mode', to: 'num'}), 'rebuild');
  assert.strictEqual(st.layout, 'num');
  assert.strictEqual(core.act({k: 'split'}), 'split');
  assert.strictEqual(st.split, false);
  assert.strictEqual(core.act({k: 'hide'}), 'hide');
  core.setField(field());
  assert.strictEqual(st.layout, 'alpha', 'a new field starts on letters');
});

console.log('ok ' + n + ' tests' + (process.exitCode ? ' (with failures)' : ''));
