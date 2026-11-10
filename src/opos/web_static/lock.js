// SPDX-License-Identifier: GPL-3.0-or-later
// Lock counts and the PIN pad. Served only while OPOS_LOCK=1, so the main page stays the same size.
window.oposLockCounts = true;
try {
  var blank = document.getElementById("lock-notice");
  if (blank) blank.textContent = "";
  var tokenEl = document.querySelector("meta[name=opos-token]");
  if (!tokenEl) throw new Error("token");
  var pin = "";
  window.oposLockHeld = true;
  function countsText(data) {
    var c = (data && data.lock) || {};
    var missed = c.missed || 0;
    var parts = [(c.messages || 0) + " new messages", missed === 1 ? "1 missed call" : missed + " missed calls"];
    if (c.alarm) parts.push(c.alarm);
    return parts.join(". ");
  }
  function showEntered() {
    var line = document.getElementById("lock-pin");
    if (!line) return;
    line.textContent = pin ? ("Entered " + pin.length) : "Enter the PIN";
  }
  function buildPad() {
    var pad = document.createElement("div");
    pad.id = "lock-pad";
    pad.setAttribute("role", "group");
    pad.setAttribute("aria-label", "PIN");
    pad.style.display = "grid";
    pad.style.gridTemplateColumns = "repeat(3, minmax(48px, 1fr))";
    pad.style.gap = "8px";
    pad.style.width = "min(100%, 280px)";
    var line = document.createElement("p");
    line.id = "lock-pin";
    line.setAttribute("role", "status");
    line.style.gridColumn = "1 / -1";
    line.style.textAlign = "center";
    line.style.minHeight = "48px";
    pad.appendChild(line);
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"].forEach(function (label) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.style.minWidth = "48px";
      button.style.minHeight = "48px";
      button.style.width = "100%";
      button.style.margin = "0";
      button.onclick = function () { onKey(label); };
      pad.appendChild(button);
    });
    var cover = document.getElementById("lock");
    var unlock = document.getElementById("unlock");
    if (cover) cover.insertBefore(pad, unlock || null);
    showEntered();
    return pad;
  }
  function paint(data) {
    var locked = !!(data && data.enforced && data.locked);
    var held = locked && !!(data && data.has_pin);
    window.oposLockCounts = locked;
    window.oposLockHeld = held;
    var note = document.getElementById("lock-notice");
    if (note && locked) note.textContent = countsText(data);
    var pad = document.getElementById("lock-pad");
    if (held) {
      if (!pad) pad = buildPad();
      pad.hidden = false;
      if (data.wait_s) {
        var wait = document.getElementById("lock-pin");
        if (wait) wait.textContent = "Wait " + data.wait_s + " seconds";
      }
    } else if (pad) pad.hidden = true;
  }
  function post(body) {
    return fetch("/api/lock", {
      method: "POST",
      headers: { "X-OPOS-Token": tokenEl.content, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
  }
  function submit() {
    var sent = pin;
    pin = "";
    showEntered();
    post({ action: "unlock", pin: sent }).then(function (res) {
      paint(res.body);
      if (res.ok && res.body && !res.body.locked) {
        window.oposLockHeld = false;
        window.oposLockCounts = false;
        if (window.oposUI && window.oposUI.unlock) window.oposUI.unlock();
        return;
      }
      var line = document.getElementById("lock-pin");
      if (!line) return;
      if (res.body && res.body.wait_s) line.textContent = "Wait " + res.body.wait_s + " seconds";
      else line.textContent = "Wrong PIN";
    }).catch(function () { window.oposLockHeld = false; });
  }
  function onKey(label) {
    if (!window.oposLockHeld) return;
    if (label === "Clear") { pin = ""; showEntered(); return; }
    if (label === "Unlock") { submit(); return; }
    if (pin.length >= 8) return;
    pin += label;
    showEntered();
  }
  function pull() {
    fetch("/api/lock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paint(data); else window.oposLockHeld = false; })
      .catch(function () { window.oposLockHeld = false; });
  }
  var unlockBtn = document.getElementById("unlock");
  if (unlockBtn && unlockBtn.addEventListener) {
    unlockBtn.addEventListener("click", function (e) {
      if (!window.oposLockHeld) return;
      if (e.preventDefault) e.preventDefault();
      if (e.stopPropagation) e.stopPropagation();
      submit();
    }, true);
  }
  pull();
  setInterval(pull, 2000);
} catch (e) { window.oposLockHeld = false; }
