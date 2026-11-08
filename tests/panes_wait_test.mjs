// SPDX-License-Identifier: GPL-3.0-or-later
// tests/panes_wait.mjs: the readiness conditions and the poll the browser driver uses. Run: node tests/panes_wait_test.mjs
import assert from 'node:assert';
import vm from 'node:vm';
import { APP_READY, DOC_READY, waitFor } from './panes_wait.mjs';

let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };
const http = { protocol: 'http:' }, done = { readyState: 'complete' };
const app = g => vm.runInNewContext(APP_READY, g);

// APP_READY is true only for a loaded http page whose oposUI can open an app.
ok(!vm.runInNewContext(DOC_READY, { document: done, location: { protocol: 'about:' } }), 'about:blank is not a page');
ok(!vm.runInNewContext(DOC_READY, { document: { readyState: 'loading' }, location: http }), 'still loading');
ok(vm.runInNewContext(DOC_READY, { document: done, location: http }), 'loaded');
ok(!app({ document: done, location: http }), 'no oposUI yet: no ReferenceError, just not ready');
ok(!app({ document: done, location: http, oposUI: {} }), 'oposUI without openApp');
ok(!app({ document: { readyState: 'loading' }, location: http, oposUI: { openApp() {} } }), 'openApp but the page is still loading');
ok(app({ document: done, location: http, oposUI: { openApp() {} } }), 'ready');
ok(/typeof oposUI\.openApp==='function'/.test(APP_READY), 'the condition names oposUI.openApp');

// waitFor polls: it returns as soon as the check holds, not after a fixed time.
let calls = 0;
const t0 = Date.now();
ok((await waitFor(() => ++calls >= 4 && 'yes', { step: 5, timeout: 5000 })) === 'yes' && calls === 4, 'returns on the fourth poll');
ok(Date.now() - t0 < 1000, 'and does not sleep a fixed time');
calls = 0;
await waitFor(() => { if (++calls < 3) throw new Error('Cannot find context'); return true; }, { step: 5 });
ok(calls === 3, 'a check that throws is "not yet"');
await assert.rejects(waitFor(() => false, { what: 'oposUI.openApp', timeout: 60, step: 10 }), /timed out after 60 ms waiting for oposUI\.openApp/);
n++;
await assert.rejects(waitFor(() => { throw new Error('boom'); }, { timeout: 40, step: 10 }), /last error: boom/);
n++;
console.log('panes_wait_test: ' + n + ' assertions passed');
