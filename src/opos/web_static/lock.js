// SPDX-License-Identifier: GPL-3.0-or-later
// Lock screen counts. Served only while OPOS_LOCK=1, so the main page stays the same size.
window.oposLockCounts = true;
try {
  var tokenEl = document.querySelector("meta[name=opos-token]");
  if (!tokenEl) throw new Error("token");
  function countsText(data) {
    var c = (data && data.lock) || {};
    var missed = c.missed || 0;
    var parts = [(c.messages || 0) + " new messages", missed === 1 ? "1 missed call" : missed + " missed calls"];
    if (c.alarm) parts.push(c.alarm);
    return parts.join(". ");
  }
  function paint(data) {
    var held = !!(data && data.enforced && data.locked);
    window.oposLockCounts = held;
    if (!held) return;
    var note = document.getElementById("lock-notice");
    if (note) note.textContent = countsText(data);
  }
  function pull() {
    fetch("/api/lock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paint(data); })
      .catch(function () {});
  }
  pull();
  setInterval(pull, 2000);
} catch (e) {}
