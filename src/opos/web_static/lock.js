// SPDX-License-Identifier: GPL-3.0-or-later
// PIN pad and lock counts. Loaded only while OPOS_LOCK=1, so page() stays the same size.
(function () {
  var tokenEl = document.querySelector("meta[name=opos-token]");
  var cover = document.getElementById("lock");
  if (!tokenEl || !cover) return;
  var pin = "";
  var pad;

  function paintCounts(data) {
    if (!data || !data.enforced || !data.locked) return;
    var c = data.lock || {};
    var parts = [];
    var messages = c.messages || 0;
    var missed = c.missed || 0;
    if (messages) parts.push(messages === 1 ? "1 new message" : messages + " new messages");
    if (missed) parts.push(missed === 1 ? "1 missed call" : missed + " missed calls");
    if (c.alarm) parts.push(c.alarm);
    var status = document.getElementById("lock-notice");
    if (status) status.textContent = parts.join(". ");
  }

  function pull() {
    fetch("/api/lock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paintCounts(data); })
      .catch(function () {});
  }

  function entered() {
    var line = document.getElementById("lock-pin");
    if (line) line.textContent = pin ? "Entered " + pin.length : "Enter the PIN";
  }

  function ensurePad() {
    if (pad) return pad;
    pad = document.createElement("div");
    pad.id = "lock-pad";
    pad.setAttribute("role", "group");
    pad.setAttribute("aria-label", "PIN");
    var line = document.createElement("p");
    line.id = "lock-pin";
    line.setAttribute("role", "status");
    pad.appendChild(line);
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"].forEach(function (label) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.style.minWidth = "48px";
      button.style.minHeight = "48px";
      button.onclick = function () { onKey(label); };
      pad.appendChild(button);
    });
    var unlock = document.getElementById("unlock");
    if (unlock && unlock.parentNode) unlock.parentNode.insertBefore(pad, unlock);
    entered();
    return pad;
  }

  function finish() {
    cover.hidden = true;
    var phone = document.getElementById("phone");
    if (phone) phone.inert = false;
    if (pad) pad.hidden = true;
    pin = "";
  }

  function submit() {
    fetch("/api/lock", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-OPOS-Token": tokenEl.content },
      body: JSON.stringify({ action: "unlock", pin: pin })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (res.ok && res.body && res.body.locked === false && res.body.has_pin) {
        finish();
        return;
      }
      pin = "";
      var line = document.getElementById("lock-pin");
      if (line) line.textContent = res.body && res.body.wait_s ? "Wait " + res.body.wait_s + " s" : "Wrong PIN";
    }).catch(function () {});
  }

  function onKey(label) {
    if (label === "Clear") pin = "";
    else if (label === "Unlock") submit();
    else if (pin.length < 8) pin += label;
    if (label !== "Unlock") entered();
  }

  var button = document.getElementById("unlock");
  if (button) {
    button.onclick = function () {
      ensurePad().hidden = false;
      entered();
    };
  }
  pull();
  setInterval(pull, 5000);
})();
