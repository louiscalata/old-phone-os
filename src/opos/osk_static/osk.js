/* SPDX-License-Identifier: GPL-3.0-or-later */
/* OPOS on-screen keyboard for the Duo OS. Served at /osk.js by opos.osk while OPOS_OSK=1; see packaging/duo-os/OSK.md.
   The logic comes first (tested under node: tests/osk_core_test.js); the part that touches the page starts at "page". */
(function (root) {
'use strict';

/* Geometry, in CSS px. osk.css uses the same numbers (tests/test_osk.py checks that they agree). */
var M = {key: 56, bar: 44, gap: 6, pad: 6, border: 1, edge: 4, halfMax: 420, padMax: 380, singleMax: 760, span: 0.75, minWindow: 840};

/* ---------- which fields get a keyboard, and which one ---------- */
var NO_TEXT = {button: 1, checkbox: 1, radio: 1, range: 1, file: 1, color: 1, submit: 1, reset: 1, image: 1, hidden: 1,
  date: 1, 'datetime-local': 1, month: 1, week: 1};

/* 'alpha' letters, 'tel' / 'int' / 'dec' number pads, 'time' the 24-hour pad; '' no keyboard (inputmode=none asks for none). */
function kindFor(tag, type, inputmode) {
  tag = String(tag || '').toUpperCase();
  type = String(type || 'text').toLowerCase();
  inputmode = String(inputmode || '').toLowerCase();
  if (inputmode === 'none') return '';
  if (tag === 'TEXTAREA') return 'alpha';
  if (tag !== 'INPUT') return '';
  if (type === 'time') return 'time';
  if (NO_TEXT[type]) return '';
  if (type === 'tel' || inputmode === 'tel') return 'tel';
  if (type === 'number' || inputmode === 'numeric') return 'int';
  if (inputmode === 'decimal') return 'dec';
  return 'alpha';
}

/* ---------- editing a value (pure) ---------- */
function insertAt(v, a, b, text, max) {
  var t;
  if (a > b) { t = a; a = b; b = t; }
  if (max > 0) {
    var room = max - (v.length - (b - a));
    if (room <= 0) return null;
    if (text.length > room) text = text.slice(0, room);
  }
  if (!text) return null;
  return {value: v.slice(0, a) + text + v.slice(b), caret: a + text.length};
}

function backspaceAt(v, a, b) {
  var t;
  if (a > b) { t = a; a = b; b = t; }
  if (a !== b) return {value: v.slice(0, a) + v.slice(b), caret: a};
  if (a === 0) return null;
  var n = 1, c = v.charCodeAt(a - 1);
  if (c >= 0xDC00 && c <= 0xDFFF && a >= 2) {
    var h = v.charCodeAt(a - 2);
    if (h >= 0xD800 && h <= 0xDBFF) n = 2;
  }
  return {value: v.slice(0, a - n) + v.slice(a), caret: a - n};
}

/* Capitals: 'sentences' (the default for text), 'words', 'characters' or 'none' (urls, e-mail, passwords, autocapitalize=off). */
function autocapMode(tag, type, attr) {
  attr = String(attr || '').toLowerCase();
  if (attr === 'none' || attr === 'off') return 'none';
  if (attr === 'words' || attr === 'characters' || attr === 'sentences') return attr;
  type = String(type || 'text').toLowerCase();
  return String(tag).toUpperCase() === 'TEXTAREA' || type === 'text' ? 'sentences' : 'none';
}

function wantsShift(before, mode) {
  if (mode === 'characters') return true;
  if (mode === 'words') return before === '' || /\s$/.test(before);
  if (mode === 'sentences') return before === '' || /[.!?]\s+$/.test(before) || /\n\s*$/.test(before);
  return false;
}

/* Shift: 0 off, 1 for the next letter, 2 caps lock (a second tap within 400 ms). */
function shiftTap(state, now, last) {
  if (state === 2) return {state: 0, last: 0};
  if (state === 1 && last && now - last < 400) return {state: 2, last: 0};
  return {state: state ? 0 : 1, last: now};
}

/* ---------- the 24-hour pad for <input type=time>: digits go into a buffer, the field gets HH:MM ---------- */
function timeAdd(buf, d) {
  if (!/^[0-9]$/.test(d) || buf.length >= 4) return null;
  var n = buf.length;
  if (n === 0) return d <= '2' ? d : '0' + d;
  if (n === 1 && buf.charAt(0) === '2' && d > '3') return null;
  if (n === 2 && d > '5') return null;
  return buf + d;
}
function timeShow(buf) {
  var s = (buf + '____').slice(0, 4);
  return s.slice(0, 2) + ':' + s.slice(2);
}
/* HH:MM from 4 digits, or from 2 or 3 (the rest zeros); '' while there is less than an hour. */
function timeValue(buf) {
  if (buf.length < 2) return '';
  var s = (buf + '0000').slice(0, 4);
  return s.slice(0, 2) + ':' + s.slice(2);
}
function timeFromValue(v) {
  var m = /^(\d\d):(\d\d)/.exec(String(v || ''));
  return m ? m[1] + m[2] : '';
}

/* ---------- layouts: every row has a left and a right half of equal weight, so the keyboard can split at the hinge ---------- */
function ch(v, w) { return {k: 'ch', v: v, w: w || 1}; }
function chars(s) { return s.split('').map(function (c) { return ch(c); }); }
function fn(k, label, w, cls, extra) {
  var o = {k: k, label: label, w: w || 1, cls: cls || 'fn'};
  if (extra) for (var i in extra) o[i] = extra[i];
  return o;
}
function sp(w) { return {k: 'space', label: 'space', aria: 'Space', w: w, cls: ''}; }
function gap(w) { return {k: 'gap', w: w || 1}; }
function bs(w) { return fn('bs', 'Delete', w, 'fn', {aria: 'Delete'}); }
function enter(w) { return fn('enter', 'Enter', w, 'go', {aria: 'Enter'}); }
function modeKey(to, label, aria, w) { return fn('mode', label, w, 'fn', {to: to, aria: aria}); }
function shiftKey(w) { return fn('shift', 'Shift', w, 'fn', {aria: 'Shift'}); }

function lastRow(to, label, aria) {
  return {l: [modeKey(to, label, aria, 1.5), ch(','), sp(2.75)], r: [sp(1.75), ch('.'), enter(2.5)]};
}
var LAYOUTS = {
  alpha: [
    {l: chars('qwert'), r: chars('yuiop')},
    {l: chars('asdfg'), r: chars("hjkl'")},
    {l: [shiftKey(1.25)].concat(chars('zxcv')), r: chars('bnm').concat([bs(2.25)])},
    lastRow('num', '123', 'Numbers and symbols')
  ],
  num: [
    {l: chars('12345'), r: chars('67890')},
    {l: chars('@#$%&'), r: chars('-+()/')},
    {l: [modeKey('sym', '#+=', 'More symbols', 1.25)].concat(chars('*"\':')), r: chars(';!?').concat([bs(2.25)])},
    lastRow('alpha', 'ABC', 'Letters')
  ],
  sym: [
    {l: chars('~`|\\^'), r: chars('{}[]_')},
    {l: chars('<>=\u00B0\u2022'), r: chars('\u20AC\u00A3\u00A5\u00A2\u00A7')},
    {l: [modeKey('num', '123', 'Numbers', 1.25)].concat(chars('\u00D7\u00F7\u00B1\u00A9')), r: chars('\u00A1\u00BF\u2026').concat([bs(2.25)])},
    lastRow('alpha', 'ABC', 'Letters')
  ]
};
function padRows(kind) {
  var d = function (n) { return ch(String(n)); };
  var x = fn('hide', 'Hide', 1, 'fn', {aria: 'Hide the keyboard'});
  if (kind === 'tel') return [[d(1), d(2), d(3), bs(1)], [d(4), d(5), d(6), ch('+')], [d(7), d(8), d(9), enter(1)], [ch('*'), d(0), ch('#'), x]];
  if (kind === 'dec') return [[d(1), d(2), d(3), bs(1)], [d(4), d(5), d(6), ch('-')], [d(7), d(8), d(9), enter(1)], [ch('.'), d(0), ch(','), x]];
  if (kind === 'time') return [[d(1), d(2), d(3), bs(1)], [d(4), d(5), d(6), fn('clear', 'Clear', 1, 'fn', {aria: 'Clear the time'})],
    [d(7), d(8), d(9), fn('done', 'Done', 1, 'go', {aria: 'Done'})], [x, ch('0', 2), gap(1)]];
  return [[d(1), d(2), d(3), bs(1)], [d(4), d(5), d(6), enter(1)], [d(7), d(8), d(9), x], [gap(1), ch('0', 2), gap(1)]];
}

/* One row for a single block: both halves side by side, the two space bars one. */
function flat(row) {
  var out = [];
  row.l.concat(row.r).forEach(function (k) {
    var last = out[out.length - 1];
    if (last && k.k === 'space' && last.k === 'space') out[out.length - 1] = sp(last.w + k.w);
    else out.push(k);
  });
  return out;
}
function sumW(keys) { return keys.reduce(function (s, k) { return s + k.w; }, 0); }
/* The narrowest real key of a row of these keys in a panel this wide. */
function minKey(keys, width) {
  var inner = width - 2 * (M.pad + M.border) - (keys.length - 1) * M.gap, unit = inner / sumW(keys), min = Infinity;
  keys.forEach(function (k) { if (k.k !== 'gap') min = Math.min(min, k.w * unit); });
  return min;
}

/* ---------- where the keyboard sits ---------- */
/* The two panes and the hinge between them, or null for a window on one pane. First that answers wins: the viewport segments
   the browser reports, then the page's --pane/--hinge (OPOS_SPAN), then the hinge the server configured (OPOS_OSK_HINGE). */
function geometry(o) {
  var pane = 0, hinge = 0, vw = o.vw;
  if (o.segs && o.segs.length === 2 && o.segs[0].w > 0) {
    pane = o.segs[0].w;
    hinge = o.segs[1].x - o.segs[0].x - o.segs[0].w;
  } else if (o.cssHinge > 0) {
    hinge = o.cssHinge;
    pane = o.cssPane > 0 ? o.cssPane : (vw - hinge) / 2;
  } else if (o.cfgHinge > 0) {
    hinge = o.cfgHinge;
    pane = (vw - hinge) / 2;
  }
  if (!(pane > 0) || !(hinge > 0) || vw < M.minWindow || vw < pane + hinge + M.span * pane) return null;
  return {pane: pane, hinge: hinge};
}

/* o: {vw, kind, split, cx, span}; span from geometry(), cx the field's horizontal centre.
   single: one panel (a window on one pane); pane: one panel on the pane that holds the field; split: a half on each pane.
   Panels hug the outer edges, where the thumbs are, and never reach the hinge. */
function plan(o) {
  var vw = o.vw, g = o.span, alpha = o.kind === 'alpha', w;
  if (!g) {
    w = Math.min(vw - 2 * M.edge, alpha ? M.singleMax : M.padMax);
    return {layout: 'single', left: Math.round((vw - w) / 2), width: w, side: 0};
  }
  var rightW = vw - g.pane - g.hinge, side = o.cx < g.pane + g.hinge / 2 ? 0 : 1, paneW = side ? rightW : g.pane;
  if (alpha && o.split) {
    return {layout: 'split', left: M.edge, width: vw - 2 * M.edge, half: Math.min(M.halfMax, Math.min(g.pane, rightW) - 2 * M.edge), side: side};
  }
  w = alpha ? paneW - 2 * M.edge : Math.min(paneW - 2 * M.edge, M.padMax);
  return {layout: 'pane', left: side ? vw - M.edge - w : M.edge, width: w, side: side};
}

/* How far to scroll (positive: content up) to keep a field of this box clear of a keyboard whose top is kbTop. */
function needScroll(top, bottom, kbTop, topEdge, margin) {
  if (bottom - top + 2 * margin > kbTop - topEdge) return top - margin - topEdge;
  if (bottom + margin > kbTop) return bottom + margin - kbTop;
  if (top - margin < topEdge) return top - margin - topEdge;
  return 0;
}

/* ---------- the keyboard's state and what a key does to a field ---------- */
var st = {field: null, kind: '', layout: 'alpha', shift: 0, shiftAt: 0, autocap: 'none', base: '', tbuf: '', tfresh: false,
  split: true, visible: false, hw: false, tap: null, plan: null, span: null, h: 0, bw: 0, bh: 0, cfgHinge: 42};

var mk = function (type, init) {
  try {
    if (/^key/.test(type) && typeof KeyboardEvent === 'function') return new KeyboardEvent(type, init);
    if ((type === 'input' || type === 'beforeinput') && typeof InputEvent === 'function') return new InputEvent(type, init);
  } catch (e) {}
  return new Event(type, init);
};

function selOf(f) {
  try {
    var a = f.selectionStart, b = f.selectionEnd;
    if (typeof a === 'number' && typeof b === 'number') return [a, b];
  } catch (e) {}
  return null;
}
function caretBefore(f) { var s = selOf(f); return f.value.slice(0, s ? s[0] : f.value.length); }

/* Insert text (or, with null, delete backwards) at the caret like the browser would: beforeinput, the new value, input. */
function edit(f, text) {
  var v = f.value, s = selOf(f), a = s ? s[0] : v.length, b = s ? s[1] : v.length, ins = text !== null;
  var r = ins ? insertAt(v, a, b, text, f.maxLength > 0 ? f.maxLength : 0) : backspaceAt(v, a, b);
  if (!r) return false;
  var type = ins ? 'insertText' : 'deleteContentBackward';
  if (!f.dispatchEvent(mk('beforeinput', {bubbles: true, cancelable: true, inputType: type, data: ins ? text : null}))) return false;
  f.value = r.value;
  if (f.value !== r.value) { f.value = v; return false; } /* the field refused it (type=number takes no partial numbers) */
  if (s) { try { f.setSelectionRange(r.caret, r.caret); } catch (e) {} }
  f.dispatchEvent(mk('input', {bubbles: true, inputType: type, data: ins ? text : null}));
  return true;
}

var CODES = {' ': 'Space', Enter: 'Enter', Backspace: 'Backspace'};
function codeOf(key) {
  if (CODES[key]) return CODES[key];
  if (/^[a-z]$/i.test(key)) return 'Key' + key.toUpperCase();
  if (/^[0-9]$/.test(key)) return 'Digit' + key;
  return '';
}
var KEYCODES = {Enter: 13, Backspace: 8, ' ': 32};
/* keydown, the action unless a handler cancelled the keydown, keyup: so the page's own key handlers see what a keyboard would send. */
function press(f, key, action) {
  var kc = KEYCODES[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
  var init = {key: key, code: codeOf(key), keyCode: kc, which: kc, bubbles: true, cancelable: true};
  var go = f.dispatchEvent(mk('keydown', init));
  var ok = go ? action() : false;
  f.dispatchEvent(mk('keyup', init));
  return ok;
}

/* The browser fires change when a field is left; a value set from script never counts, so the keyboard does it. */
function commit(f) {
  if (f && f.value !== st.base) {
    st.base = f.value;
    f.dispatchEvent(mk('change', {bubbles: true}));
  }
}

function autoShift() {
  if (st.shift === 2 || st.kind !== 'alpha' || !st.field) return;
  st.shift = wantsShift(caretBefore(st.field), st.autocap) ? 1 : 0;
}

function timeSet(f, v) {
  f.value = v;
  f.dispatchEvent(mk('input', {bubbles: true}));
  commit(f);
}
function timeDigit(d) {
  var f = st.field, n;
  if (st.tfresh) { st.tbuf = ''; st.tfresh = false; }
  n = timeAdd(st.tbuf, d);
  if (n === null) return;
  st.tbuf = n;
  if (n.length === 4) timeSet(f, timeValue(n));
}

/* Hide or a tap elsewhere with two or three digits typed: the field gets them as Done would, rather than silently dropping them. */
function timeFlush(f) {
  var v;
  if (st.kind !== 'time' || st.tfresh || st.tbuf.length < 2) return;
  v = timeValue(st.tbuf);
  st.tbuf = timeFromValue(v);
  st.tfresh = true;
  if (v !== f.value) timeSet(f, v);
}

function typeChar(c) {
  var f = st.field;
  if (st.kind === 'time') { timeDigit(c); return; }
  if (st.shift && /^[a-z]$/.test(c)) c = c.toUpperCase();
  if (press(f, c, function () { return edit(f, c); })) {
    if (st.shift === 1) st.shift = 0;
    autoShift();
  }
}

function typeBackspace() {
  var f = st.field;
  if (st.kind === 'time') {
    st.tbuf = st.tfresh ? '' : st.tbuf.slice(0, -1);
    st.tfresh = false;
    return;
  }
  press(f, 'Backspace', function () { return edit(f, null); });
  autoShift();
}

function typeEnter() {
  var f = st.field;
  press(f, 'Enter', function () {
    if (String(f.tagName).toUpperCase() === 'TEXTAREA') return edit(f, '\n');
    commit(f);
    if (f.form && typeof f.form.requestSubmit === 'function') { try { f.form.requestSubmit(); } catch (e) {} }
    return true;
  });
  autoShift();
}

/* One key press. spec is an entry of a layout; returns what the page part has to do next: '', 'hide', 'rebuild', 'relabel' or 'info'. */
function act(spec) {
  var f = st.field;
  if (!f) return '';
  switch (spec.k) {
    case 'ch': typeChar(spec.v); return st.kind === 'time' ? 'info' : 'relabel';
    case 'space': typeChar(' '); return 'relabel';
    case 'bs': typeBackspace(); return st.kind === 'time' ? 'info' : 'relabel';
    case 'enter': typeEnter(); return 'relabel';
    case 'shift':
      var t = shiftTap(st.shift, Date.now(), st.shiftAt);
      st.shift = t.state; st.shiftAt = t.last;
      return 'relabel';
    case 'mode': st.layout = spec.to; return 'rebuild';
    case 'split': st.split = !st.split; return 'split';
    case 'clear':
      st.tbuf = ''; st.tfresh = false;
      if (f.value !== '') timeSet(f, '');
      return 'info';
    case 'done':
      if (st.tbuf.length >= 2) timeSet(f, timeValue(st.tbuf));
      return 'hide';
    case 'hide': return 'hide';
  }
  return '';
}

/* Make f the field the keys type into. */
function setField(f) {
  st.field = f;
  st.kind = kindFor(f.tagName, f.type, f.getAttribute && f.getAttribute('inputmode'));
  st.layout = 'alpha';
  st.base = f.value;
  st.autocap = autocapMode(f.tagName, f.type, f.getAttribute && f.getAttribute('autocapitalize'));
  st.tbuf = st.kind === 'time' ? timeFromValue(f.value) : '';
  st.tfresh = st.kind === 'time';
  if (st.shift !== 2) st.shift = 0;
  autoShift();
}

var core = {M: M, kindFor: kindFor, insertAt: insertAt, backspaceAt: backspaceAt, autocapMode: autocapMode, wantsShift: wantsShift,
  shiftTap: shiftTap, timeAdd: timeAdd, timeShow: timeShow, timeValue: timeValue, timeFromValue: timeFromValue,
  LAYOUTS: LAYOUTS, padRows: padRows, flat: flat, sumW: sumW, minKey: minKey, geometry: geometry, plan: plan, needScroll: needScroll,
  st: st, act: act, setField: setField, commit: commit, edit: edit, timeFlush: timeFlush,
  setEventFactory: function (f) { mk = f; }};

/* ---------- page: the keyboard itself ---------- */
var ui = null, pollT = 0, hideT = 0, rafT = 0, reps = {}, downs = {};

function $el(tag, cls) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function eligible(f) {
  if (!f || f.nodeType !== 1 || f.disabled || f.readOnly) return false;
  var tag = String(f.tagName).toUpperCase();
  if (tag !== 'INPUT' && tag !== 'TEXTAREA') return false;
  return kindFor(tag, f.type, f.getAttribute('inputmode')) !== '';
}
function shown(f) { return !!(f && f.isConnected && f.getClientRects().length); }

var NAMES = {',': 'comma', '.': 'period', "'": 'apostrophe', '@': 'at', '#': 'number sign', '$': 'dollar', '%': 'percent', '&': 'ampersand',
  '-': 'minus', '+': 'plus', '(': 'left parenthesis', ')': 'right parenthesis', '/': 'slash', '*': 'asterisk', '"': 'quotation mark',
  ':': 'colon', ';': 'semicolon', '!': 'exclamation mark', '?': 'question mark', '~': 'tilde', '`': 'grave accent', '|': 'vertical bar',
  '\\': 'backslash', '^': 'caret', '{': 'left brace', '}': 'right brace', '[': 'left bracket', ']': 'right bracket', '_': 'underscore',
  '<': 'less than', '>': 'greater than', '=': 'equals'};

function keyBtn(spec) {
  var b;
  if (spec.k === 'gap') {
    b = $el('div', 'osk-gap');
    b.style.flexGrow = String(spec.w);
    b.setAttribute('aria-hidden', 'true');
    return b;
  }
  b = $el('button', 'osk-key' + (spec.cls ? ' ' + spec.cls : ''));
  b.type = 'button';
  b.tabIndex = -1;
  b.style.flexGrow = String(spec.w);
  b.setAttribute('data-i', String(ui.keys.length));
  ui.keys.push(spec);
  ui.btns.push(b);
  if (spec.k === 'ch') {
    b.textContent = spec.v;
    if (NAMES[spec.v]) b.setAttribute('aria-label', NAMES[spec.v]);
  } else {
    b.textContent = spec.label;
    if (spec.aria) b.setAttribute('aria-label', spec.aria);
  }
  if (spec.k === 'shift') b.setAttribute('aria-pressed', 'false');
  return b;
}

function relabel() {
  if (!ui) return;
  ui.btns.forEach(function (b, i) {
    var s = ui.keys[i];
    if (s.k === 'ch' && /^[a-z]$/.test(s.v)) b.textContent = st.shift ? s.v.toUpperCase() : s.v;
    if (s.k === 'shift') {
      b.setAttribute('aria-pressed', st.shift ? 'true' : 'false');
      if (st.shift === 2) b.setAttribute('data-caps', ''); else b.removeAttribute('data-caps');
      b.textContent = st.shift === 2 ? 'CAPS' : 'Shift';
    }
  });
}
function info() {
  if (!ui || !ui.info) return;
  ui.info.textContent = st.kind === 'time' ? timeShow(st.tbuf) : '';
}

function rowEl(keys) {
  var r = $el('div', 'osk-row');
  keys.forEach(function (k) { r.appendChild(keyBtn(k)); });
  return r;
}
function barBtn(spec, text, aria) {
  var b = keyBtn({k: spec, label: text, aria: aria, w: 0, cls: 'fn bar'});
  b.style.flexGrow = '0';
  return b;
}

function spanNow() {
  var de = document.documentElement, segs = null, cs = null, probe;
  try {
    var vv = root.visualViewport;
    if (vv && vv.segments && vv.segments.length === 2) segs = [{x: vv.segments[0].x, w: vv.segments[0].width}, {x: vv.segments[1].x, w: vv.segments[1].width}];
    else if (typeof root.getWindowSegments === 'function') {
      var ws = root.getWindowSegments();
      if (ws && ws.length === 2) segs = [{x: ws[0].left, w: ws[0].width}, {x: ws[1].left, w: ws[1].width}];
    }
  } catch (e) {}
  if (!segs) {
    try {
      if (root.matchMedia('(horizontal-viewport-segments: 2)').matches) {
        probe = $el('div');
        probe.style.cssText = 'position:fixed;top:0;visibility:hidden;pointer-events:none;height:1px;width:env(viewport-segment-width 0 0,0px);left:env(viewport-segment-left 1 0,0px)';
        document.body.appendChild(probe);
        var w0 = probe.offsetWidth, l1 = probe.offsetLeft;
        document.body.removeChild(probe);
        if (w0 > 0 && l1 > w0) segs = [{x: 0, w: w0}, {x: l1, w: 0}];
      }
    } catch (e) {}
  }
  try { cs = root.getComputedStyle(de); } catch (e) {}
  var num = function (n) { var v = cs ? parseFloat(cs.getPropertyValue(n)) : 0; return v > 0 ? v : 0; };
  return geometry({vw: de.clientWidth, segs: segs, cssHinge: num('--hinge'), cssPane: num('--pane'), cfgHinge: st.cfgHinge});
}

function build() {
  var r = ui.root, p, rows, L, R, one, grid;
  r.textContent = '';
  ui.keys = []; ui.btns = []; ui.info = null;
  st.span = spanNow();
  st.bw = document.documentElement.clientWidth;
  st.bh = root.innerHeight;
  var fr = st.field.getBoundingClientRect();
  p = st.plan = plan({vw: st.bw, kind: st.kind, split: st.split, cx: fr.left + fr.width / 2, span: st.span});
  grid = $el('div', 'osk-grid osk-' + p.layout);
  r.appendChild(grid);
  var mkPanel = function (side) { var d = $el('div', 'osk-panel osk-' + side); grid.appendChild(d); return d; };
  var mkInfo = function () { ui.info = $el('div', 'osk-info'); if (st.kind === 'time') ui.info.setAttribute('aria-live', 'polite'); return ui.info; };
  if (p.layout === 'split') {
    grid.style.gridTemplateColumns = p.half + 'px 1fr ' + p.half + 'px';
    grid.style.margin = '0 ' + M.edge + 'px';
    L = mkPanel('l'); R = mkPanel('r');
    R.style.gridColumn = '3';
    var bl = $el('div', 'osk-row osk-barrow');
    bl.appendChild(barBtn('hide', 'Hide', 'Hide the keyboard'));
    bl.appendChild(mkInfo());
    L.appendChild(bl);
    var br = $el('div', 'osk-row osk-barrow');
    br.appendChild($el('div', 'osk-info'));
    br.appendChild(barBtn('split', 'Join', 'Put the keyboard on one screen'));
    R.appendChild(br);
    LAYOUTS[st.layout].forEach(function (row) { L.appendChild(rowEl(row.l)); R.appendChild(rowEl(row.r)); });
  } else {
    grid.style.marginLeft = p.left + 'px';
    grid.style.width = p.width + 'px';
    one = mkPanel('l');
    if (st.kind === 'alpha' || st.kind === 'time') {
      var b = $el('div', 'osk-row osk-barrow');
      if (st.kind === 'alpha') b.appendChild(barBtn('hide', 'Hide', 'Hide the keyboard'));
      b.appendChild(mkInfo());
      if (st.kind === 'alpha' && st.span) b.appendChild(barBtn('split', 'Split', 'Split the keyboard across both screens'));
      one.appendChild(b);
    }
    rows = st.kind === 'alpha' ? LAYOUTS[st.layout].map(flat) : padRows(st.kind);
    rows.forEach(function (keys) { one.appendChild(rowEl(keys)); });
  }
  relabel();
  info();
}

/* Keep the focused field clear of the keyboard: scroll whatever scrolls, nearest first. */
function reveal() {
  var f = st.field;
  if (!ui || !st.visible || !f) return;
  var g = ui.root.firstChild;
  st.h = g ? Math.ceil(g.getBoundingClientRect().height) : 0;
  document.documentElement.style.setProperty('--osk-h', st.h + 'px');
  if (!ui.pad) { ui.pad = $el('div'); ui.pad.id = 'opos-osk-pad'; ui.pad.setAttribute('aria-hidden', 'true'); document.body.appendChild(ui.pad); }
  ui.pad.style.height = st.h + 'px';
  try { f.scrollIntoView({block: 'nearest', inline: 'nearest'}); } catch (e) {}
  var r = f.getBoundingClientRect(), d = needScroll(r.top, r.bottom, root.innerHeight - st.h, 36, 12);
  for (var a = f.parentElement; a && d; a = a.parentElement) {
    if (!/(auto|scroll)/.test(root.getComputedStyle(a).overflowY) || a.scrollHeight <= a.clientHeight) continue;
    var before = a.scrollTop;
    a.scrollTop = before + d;
    d -= a.scrollTop - before;
  }
  if (d && document.scrollingElement) document.scrollingElement.scrollTop += d;
}

function stopRepeat(id) {
  var o = reps[id];
  if (o) { clearTimeout(o.t1); clearInterval(o.t2); delete reps[id]; }
}
function stopAll() { Object.keys(reps).forEach(stopRepeat); }

function show() {
  if (!st.field || st.hw) return;
  ensureUi();
  clearTimeout(hideT);
  st.visible = true;
  ui.root.hidden = false;
  build();
  reveal();
  clearInterval(pollT);
  pollT = setInterval(poll, 500);
}

function hide(blur) {
  var f = st.field;
  clearTimeout(hideT);
  clearInterval(pollT);
  stopAll();
  if (f && st.visible) { timeFlush(f); commit(f); }
  st.visible = false;
  if (ui) {
    ui.root.hidden = true;
    if (ui.pad && ui.pad.parentNode) ui.pad.parentNode.removeChild(ui.pad);
    ui.pad = null;
  }
  document.documentElement.style.removeProperty('--osk-h');
  if (blur && f && document.activeElement === f) f.blur();
}

function poll() {
  var a = document.activeElement;
  if (!st.visible) return;
  if (!eligible(a) || !shown(a)) { hide(false); return; }
  if (a !== st.field) { setField(a); build(); reveal(); }
}

/* The window changed size (rotation, a new output); a pinch zoom, which also resizes the visual viewport, changes neither number. */
function relayout() {
  if (rafT || !st.visible) return;
  rafT = root.requestAnimationFrame(function () {
    rafT = 0;
    if (!st.visible || !st.field) return;
    if (document.documentElement.clientWidth !== st.bw || root.innerHeight !== st.bh) build();
    reveal();
  });
}

function run(spec) {
  var res = act(spec);
  if (res === 'hide') hide(true);
  else if (res === 'rebuild' || res === 'split') {
    if (res === 'split') { try { root.localStorage.setItem('opos_osk_split', st.split ? '1' : '0'); } catch (e) {} }
    build();
    reveal();
  } else if (res === 'relabel' || res === 'info') { relabel(); info(); }
}

function keyOf(t) { return t && t.closest ? t.closest('.osk-key[data-i]') : null; }

function ensureUi() {
  if (ui) return;
  var r = $el('div');
  r.id = 'opos-osk';
  r.hidden = true;
  r.setAttribute('role', 'group');
  r.setAttribute('aria-label', 'On-screen keyboard');
  document.body.appendChild(r);
  ui = {root: r, keys: [], btns: [], info: null, pad: null};
  /* Pressing a key must not take the focus from the field, whatever the pointer. */
  r.addEventListener('pointerdown', function (e) {
    var b = keyOf(e.target), spec;
    e.preventDefault();
    if (!b || e.button > 0) return;
    spec = ui.keys[+b.getAttribute('data-i')];
    b.classList.add('on');
    b._down = e.pointerId;
    run(spec);
    if (spec.k === 'bs' && st.visible) {
      stopRepeat(e.pointerId);
      var o = reps[e.pointerId] = {};
      o.t1 = setTimeout(function () { o.t2 = setInterval(function () { if (st.field && st.visible) run(spec); }, 55); }, 420);
    }
    var f = st.field;
    if (f && st.visible && document.activeElement !== f) { try { f.focus({preventScroll: true}); } catch (x) {} }
  });
  r.addEventListener('mousedown', function (e) { e.preventDefault(); });
  r.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  /* A click with no pointer behind it (detail 0) is a keyboard or assistive-technology activation. */
  r.addEventListener('click', function (e) {
    var b = keyOf(e.target);
    if (!b) return;
    e.preventDefault();
    if (e.detail === 0) run(ui.keys[+b.getAttribute('data-i')]);
  });
}

function onPointerDown(e) {
  downs[e.pointerId] = Date.now();
  if (e.pointerType === 'touch' || e.pointerType === 'pen') st.hw = false;
  var t = e.target;
  st.tap = {t: Date.now(), el: t && t.closest ? t.closest('input,textarea,label') : null};
}
function onPointerEnd(e) {
  delete downs[e.pointerId];
  stopRepeat(e.pointerId);
  if (ui) ui.root.querySelectorAll('.osk-key.on').forEach(function (b) { if (b._down === e.pointerId) b.classList.remove('on'); });
}
/* A finger is down: the layout must hold still until the tap has landed (ids older than 3 s are lost events). */
function pointerBusy() {
  var now = Date.now();
  return Object.keys(downs).some(function (k) { return now - downs[k] < 3000; });
}
function tappedField(f) {
  var tp = st.tap;
  if (!tp || !tp.el || Date.now() - tp.t > 1000) return false;
  return tp.el === f || (String(tp.el.tagName).toUpperCase() === 'LABEL' && tp.el.control === f);
}
function onFocusIn(e) {
  var f = e.target;
  if (!eligible(f)) return;
  clearTimeout(hideT);
  var again = st.visible;
  setField(f);
  if (again || tappedField(f)) show();
}
function hideCheck() {
  if (pointerBusy()) { hideT = setTimeout(hideCheck, 100); return; }
  var a = document.activeElement;
  if (eligible(a)) { if (a !== st.field) { setField(a); build(); reveal(); } } else hide(false);
}
function onFocusOut(e) {
  var f = e.target;
  if (f === st.field) commit(f);
  if (!st.visible) return;
  clearTimeout(hideT);
  hideT = setTimeout(hideCheck, 80);
}
function onClick(e) {
  var f = e.target;
  if (eligible(f) && document.activeElement === f && !st.visible && e.isTrusted) { setField(f); show(); }
}
function onNativeChange(e) { if (e.target === st.field) st.base = st.field.value; }
function onHwKey(e) {
  if (!e.isTrusted || !e.key || e.key === 'Unidentified' || e.isComposing || e.keyCode === 229) return;
  if (e.key.length === 1 || /^(Backspace|Delete|Enter|Tab|Escape|Arrow(Left|Right|Up|Down)|Home|End)$/.test(e.key)) {
    st.hw = true;
    if (st.visible) hide(false);
  }
}

function init() {
  var s = document.currentScript || document.querySelector('script[src="/osk.js"]');
  var h = s ? parseInt(s.getAttribute('data-hinge'), 10) : NaN;
  if (h >= 0 && h <= 200) st.cfgHinge = h;
  try { st.split = root.localStorage.getItem('opos_osk_split') !== '0'; } catch (e) {}
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('pointerup', onPointerEnd, true);
  document.addEventListener('pointercancel', onPointerEnd, true);
  document.addEventListener('focusin', onFocusIn, true);
  document.addEventListener('focusout', onFocusOut, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('change', onNativeChange, true);
  document.addEventListener('keydown', onHwKey, true);
  document.addEventListener('visibilitychange', function () { if (document.hidden && st.visible) hide(false); });
  root.addEventListener('resize', relayout, {passive: true});
  root.addEventListener('orientationchange', relayout, {passive: true});
  if (root.visualViewport) root.visualViewport.addEventListener('resize', relayout, {passive: true});
  core.show = show;
  core.hide = hide;
  core.ui = function () { return ui; };
  root.oposOsk = core;
}

if (typeof module === 'object' && module && module.exports) module.exports = core;
if (typeof document !== 'undefined' && document && document.documentElement) {
  try {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  } catch (e) {}
}
})(typeof window !== 'undefined' ? window : globalThis);
