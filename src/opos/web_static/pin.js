// SPDX-License-Identifier: GPL-3.0-or-later
// The PIN pad. Loaded only while OPOS_LOCK=1, so page() stays the same size.
// Without this script the lock screen is a cover in the page and nothing more. With it, showing the cover locks the
// server, and the cover comes off only after the server has answered that the phone is unlocked. The server decides;
// this script never compares a PIN and never keeps one after it was sent.
(function () {
  var tokenEl = document.querySelector("meta[name=opos-token]");
  var cover = document.getElementById("lock");
  var opener = document.getElementById("unlock");
  var ui = window.oposUI;
  if (!tokenEl || !cover || !opener || !ui || typeof ui.unlock !== "function" || typeof ui.lock !== "function") return;
  var uncover = ui.unlock;
  var coverUp = ui.lock;
  var pin = "";
  var pad = null;
  var line = null;
  var busy = false;
  var opened = 0;

  function ask(body) {
    var options = { headers: { "X-OPOS-Token": tokenEl.content } };
    if (body) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch("/api/lock", options).then(function (r) { return r.json(); });
  }

  function say(text) {
    if (line && line.textContent !== text) line.textContent = text;
  }

  function entered() {
    say(pin ? pin.length + (pin.length === 1 ? " digit entered" : " digits entered") : "Enter the PIN");
  }

  function waitText(seconds) {
    if (seconds >= 3600) return "about " + Math.ceil(seconds / 3600) + " h";
    if (seconds >= 120) return Math.ceil(seconds / 60) + " min";
    return seconds + " s";
  }

  function waiting(status) {
    return "Too many wrong tries. Wait " + waitText(status.wait_s) + ".";
  }

  function hidePad() {
    pin = "";
    if (pad) pad.hidden = true;
    opener.hidden = false;
  }

  function done() {
    opened += 1;
    hidePad();
    uncover.call(ui);
  }

  function press(label) {
    if (busy) return;
    if (label === "Unlock") {
      submit();
      return;
    }
    if (label === "Clear") pin = "";
    else if (pin.length < 8) pin += label;
    entered();
  }

  function build() {
    pad = document.createElement("div");
    pad.id = "lock-pad";
    pad.setAttribute("role", "group");
    pad.setAttribute("aria-label", "PIN pad");
    pad.style.cssText = "display:grid;grid-template-columns:repeat(3,1fr);gap:8px;width:min(100%,360px)";
    line = document.createElement("p");
    line.id = "lock-pin";
    line.setAttribute("role", "status");
    line.setAttribute("aria-live", "polite");
    line.style.cssText = "grid-column:1/-1;margin:0;min-height:1.5em;text-align:center";
    pad.appendChild(line);
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"].forEach(function (label) {
      var key = document.createElement("button");
      key.type = "button";
      key.textContent = label;
      key.style.cssText = "min-height:56px;margin:0";
      key.onclick = function () { press(label); };
      pad.appendChild(key);
    });
    opener.parentNode.insertBefore(pad, opener);
  }

  function showPad(status) {
    if (!pad) build();
    pin = "";
    pad.hidden = false;
    opener.hidden = true;
    if (status && status.wait_s > 0) say(waiting(status));
    else entered();
  }

  function submit() {
    if (busy) return;
    if (pin.length < 4) {
      pin = "";
      say("A PIN has 4 to 8 digits");
      return;
    }
    var tried = pin;
    pin = "";
    busy = true;
    ask({ action: "unlock", pin: tried }).then(function (status) {
      if (status && status.locked === false) done();
      else say(status && status.wait_s > 0 ? waiting(status) : "Wrong PIN");
    }).catch(function () {
      say("The phone did not answer. Try again.");
    }).then(function () { busy = false; });
  }

  // Every way the page takes the cover off (the Unlock control, a swipe up, Escape, the Call and Helper buttons, the
  // page's own error handlers) goes through window.oposUI.unlock, so this is the one gate.
  function open() {
    if (cover.hidden) {
      uncover.call(ui);
      return;
    }
    if (busy) return;
    busy = true;
    ask().then(function (status) {
      if (status && status.locked === false) {
        done();
        return null;
      }
      if (!status || status.locked !== true) throw new Error("lock");
      if (status.has_pin === true) {
        showPad(status);
        return null;
      }
      // No PIN is saved: the server unlocks without digits, and only its answer takes the cover off.
      return ask({ action: "unlock", pin: "" }).then(function (after) {
        if (!after || after.locked !== false) throw new Error("lock");
        done();
      });
    }).catch(function () {
      if (!pad) build();
      pad.hidden = false;
      say("The lock did not answer. Reload the page.");
    }).then(function () { busy = false; });
  }

  function lockServer() {
    ask({ action: "lock" }).catch(function () {});
  }

  ui.unlock = function () { open(); };
  ui.lock = function () {
    var result = coverUp.apply(ui, arguments);
    hidePad();
    if (!cover.hidden) lockServer();
    return result;
  };

  function check() {
    var before = opened;
    ask().then(function (status) {
      // The server is locked and the page shows the phone: cover it. Its data is already withheld by the server.
      // An answer asked for before an unlock is out of date and is dropped; the next check asks again.
      if (status && status.locked === true && cover.hidden && before === opened && !busy) coverUp.call(ui);
    }).catch(function () {});
  }

  if (!cover.hidden) lockServer();
  check();
  setInterval(check, 5000);
})();
