/*
# SPDX-License-Identifier: GPL-3.0-or-later
*/
// SPDX-License-Identifier: GPL-3.0-or-later
// Waiting for a page, not for the clock. tests/panes_browser_driver.mjs polls these conditions after every load instead of sleeping a
// fixed time: on a busy machine a page can take longer than any fixed time, and then the driver calls into a page that is not there yet.
// A condition is a page expression (a string); waitFor polls any async check.
export const sleep = ms => new Promise(r => setTimeout(r, ms));

// A real page (not about:blank) has finished loading.
export const DOC_READY = "document.readyState==='complete'&&location.protocol==='http:'";
// The kiosk page's own script has run far enough to open an app (oposUI.openApp is what the driver calls first).
export const APP_READY = DOC_READY + "&&typeof oposUI!=='undefined'&&typeof oposUI.openApp==='function'";

// Poll `check` every `step` ms until it returns something truthy; return that. A check that throws counts as "not yet" (the page can be
// between two documents), and the last such error is named if the time runs out.
export async function waitFor(check, { what = 'the page', timeout = 20000, step = 100 } = {}) {
  const t0 = Date.now();
  let last = null;
  for (;;) {
    try { const v = await check(); if (v) return v; last = null; } catch (e) { last = e; }
    if (Date.now() - t0 >= timeout) throw new Error('timed out after ' + timeout + ' ms waiting for ' + what + (last ? ' (last error: ' + last.message + ')' : ''));
    await sleep(step);
  }
}
