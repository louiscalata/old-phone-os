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
  var lastPeople = [];
  var openedPractice = false;
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
  var pendingPractice = null;
  function lockCoverOn() {
    var cover = document.getElementById("lock");
    return !!(cover && !cover.hidden);
  }
  function showPractice(which, number) {
    var ui = window.oposUI;
    ui.openApp(which);
    var field = document.getElementById("number");
    if (field) field.value = number || "";
    var line = which === "messages" ? "Practice message. Nothing was sent." : "Practice call. No real call is made.";
    if (typeof showNotice === "function") showNotice(line, which);
  }
  function openPractice(which, number) {
    if (which !== "phone" && which !== "messages") return;
    var ui = window.oposUI;
    if (!ui || typeof ui.openApp !== "function") return;
    if (lockCoverOn()) {
      pendingPractice = { which: which, number: number || "" };
      return;
    }
    pendingPractice = null;
    showPractice(which, number);
  }
  function resumePractice() {
    if (!pendingPractice || lockCoverOn()) return;
    var next = pendingPractice;
    pendingPractice = null;
    openPractice(next.which, next.number);
  }
  function watchUnlock() {
    var ui = window.oposUI;
    if (!ui || typeof ui.unlock !== "function" || ui.unlock.oposPractice) return;
    var base = ui.unlock;
    function wrapped() {
      var result = base.apply(this, arguments);
      try { resumePractice(); } catch (err) {}
      return result;
    }
    wrapped.oposPractice = true;
    ui.unlock = wrapped;
  }
  watchUnlock();
  function fromQuery() {
    if (openedPractice) return;
    var which = "";
    var number = "";
    try {
      var q = new URLSearchParams(location.search);
      which = q.get("practice") || "";
      number = q.get("n") || "";
    } catch (e) {
      return;
    }
    if (which !== "phone" && which !== "messages") return;
    openedPractice = true;
    openPractice(which, number);
  }
  function paintFav(people) {
    var home = document.getElementById("home");
    if (!home) return;
    var old = home.querySelectorAll("[data-fav]");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
    var box = document.getElementById("home-favourites") || home;
    (people || []).forEach(function (p) {
      if (!p || !p.favorite || !p.name) return;
      var b = document.createElement("button");
      var num = (p.numbers && p.numbers[0] && p.numbers[0].number) || "";
      b.type = "button";
      b.setAttribute("data-fav", "1");
      b.textContent = p.name;
      b.setAttribute("aria-label", p.name + ", favourite");
      b.addEventListener("click", function () { openPractice("phone", num); });
      box.appendChild(b);
    });
  }
  function pull() {
    fetch("/api/clock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paintAlarm(data); })
      .catch(function () {});
  }
  function contactsTile(on) {
    document.documentElement.setAttribute("data-contacts", on ? "1" : "0");
    var buttons = document.querySelectorAll("#apps-list button, #home button");
    for (var i = 0; i < buttons.length; i++) {
      if ((buttons[i].textContent || "").trim() !== "Contacts") continue;
      var row = buttons[i].parentNode;
      if (row && row.tagName === "LI") row.hidden = !on;
      else buttons[i].hidden = !on;
    }
  }
  function pullPeople() {
    fetch("/api/contacts", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) {
          contactsTile(false);
          return;
        }
        contactsTile(true);
        lastPeople = data.people || [];
        paintFav(lastPeople);
      })
      .catch(function () { contactsTile(false); });
  }
  window.oposHooks = window.oposHooks || [];
  window.oposHooks.push(function () { if (window.__oposAlarm) paintAlarm({ clock_ok: true, now: new Date().toISOString(), next: { text: window.__oposAlarm, at: new Date(Date.now() + 60000).toISOString() } }); });
  window.oposHooks.push(function () {
    try { watchUnlock(); } catch (e) {}
    try { fromQuery(); } catch (e) {}
    try { resumePractice(); } catch (e) {}
    try { if (lastPeople.length) paintFav(lastPeople); } catch (e) {}
  });
  pull();
  pullPeople();
  setInterval(pull, 30000);
  setInterval(pullPeople, 30000);
  function lockLine(data) {
    if (!data || !data.enforced || !data.locked) return;
    var note = document.getElementById("lock-notice");
    if (!note) return;
    var c = data.lock || {};
    var missed = c.missed || 0;
    var parts = [(c.messages || 0) + " new messages", missed === 1 ? "1 missed call" : missed + " missed calls"];
    if (c.alarm) parts.push(c.alarm);
    note.textContent = parts.join(". ");
  }
  function pullLock() {
    fetch("/api/lock", { headers: { "X-OPOS-Token": tokenEl.content } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(lockLine)
      .catch(function () {});
  }
  pullLock();
  setInterval(pullLock, 2000);
} catch (e) {}
