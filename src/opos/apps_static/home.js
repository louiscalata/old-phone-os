// SPDX-License-Identifier: GPL-3.0-or-later
// Home additions for everyday apps. Loaded only when OPOS_APPS names an app.
try {
  var list = document.getElementById("apps-list");
  if (list && !document.getElementById("more-apps")) {
    var link = document.createElement("a");
    var item = document.createElement("li");
    link.id = "more-apps";
    link.href = "/apps";
    link.textContent = "More apps";
    link.style.cssText = "display:block;min-height:48px;line-height:48px;color:inherit;text-decoration:underline";
    item.appendChild(link);
    list.appendChild(item);
  }
} catch (e) {}
try {
  var tokenEl = document.querySelector("meta[name=opos-token]");
  if (!tokenEl) throw new Error("token");
  function paintAlarm(data) {
    var text = "";
    if (data && data.clock_ok && data.next && data.next.text && data.next.at) {
      var at = new Date(data.next.at);
      var now = new Date(data.now || Date.now());
      if (at >= now && at - now <= 24 * 60 * 60 * 1000) text = data.next.text;
    }
    window.__oposAlarm = text;
    var date = document.getElementById("lock-date");
    if (date) {
      var line = document.getElementById("lock-alarm");
      if (!line) {
        line = document.createElement("div");
        line.id = "lock-alarm";
        date.parentNode.insertBefore(line, date.nextSibling);
      }
      line.textContent = text;
    }
    var engine = document.getElementById("sb-engine");
    if (engine) {
      var cur = engine.textContent.replace(/\s*Alarm$/, "");
      engine.textContent = text ? (cur ? cur + " " : "") + "Alarm" : cur;
    }
  }
  function pull() {
    fetch("/api/clock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paintAlarm(data); })
      .catch(function () {});
  }
  window.oposHooks = window.oposHooks || [];
  window.oposHooks.push(function () { if (window.__oposAlarm) paintAlarm({ clock_ok: true, now: new Date().toISOString(), next: { text: window.__oposAlarm, at: new Date(Date.now() + 60000).toISOString() } }); });
  pull();
  setInterval(pull, 30000);
} catch (e) {}
