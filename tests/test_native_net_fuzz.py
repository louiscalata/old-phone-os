# SPDX-License-Identifier: GPL-3.0-or-later
"""A seeded state-machine fuzzer for the native Network screen (screens.Network) on the stand-in toolkit.

A small invented world (four fake networks, two of them saved) plays the core. Each step is one random event: a request
the page sent is answered by the core, an answer is delivered to the page, a change that had no answer in time takes
effect late, the network changes outside the page (another network joined from the browser's /network page,
NetworkManager roaming, the Wi-Fi switched or a network dropped; more often while the page is left), the owner looks at
the page or taps something (a row, a button, a key, or an edit of the name typed for a hidden network), the page polls,
is left or is entered again. Requests are
processed and answers delivered in any order, as threads and the window's loop can do. After every step the fuzzer
checks what the page holds, and at every change the page sends it checks why the page sent it:

- no password escapes: a password is sent only with the network it was typed for, exactly as typed, and the typed
  password is held only while a typing pane shows (never after choosing another network, cancelling or leaving, and
  never after the name typed for a hidden network is edited);
- no two changes (join, forget, Wi-Fi) are in flight at once;
- no change without an informed tap: a tap on the main button or the Wi-Fi button acts only on what the button has said
  (and whether it was there) for at least HOLD_S, unless the owner's own tap made it say that; a tap on a row chooses
  it only when the list has had that network in that place, offering the same, for at least HOLD_S;
- no stale reply wins: Disconnect (which names no network to the core) and the Wi-Fi switch are never sent on a radios
  read made before a change whose answer was delivered, nor on a radios read drawn in an earlier visit (the network may
  have changed while the page was left), and Disconnect never drops a network other than the chosen one on a read that
  is current. Disconnect is sent only on a radios read the core made after the confirming tap, so a roam between the two
  taps is seen before anything is sent. Whether the chosen row is the connected one follows whichever of the radios
  read and the list drawn was asked later (each request is numbered here as it is sent), never the one asked earlier.

Everything is deterministic: the seeds are fixed, nothing sleeps, and no thread, socket, file or radio is used.
"""
import collections
import io
import random
import unittest
import urllib.error

from opos.native import netlogic
from tests import test_native_net as base

SEEDS = 1000
STEPS = 200
PW = base.PW
HIDDEN_NAME = "Hid-Test"
CORE_SLOW = "The network service did not answer in time. Try again."     # opos.net's own timeout sentence
OUTCOMES = ("ok", "refused", "http429", "late", "late_effect", "core_slow", "core_slow_effect", "dropped", "no_answer")


class World:
    """The truth the stand-in core reads and changes. `t` counts the core's steps, so every read says when it was made."""

    def __init__(self, rnd):
        self.rnd, self.t = rnd, 0
        self.on, self.conn = True, "Home-Test"
        self.saved = {"Home-Test", "Cafe-Test"}
        self.near = ("Home-Test", "Cafe-Test", "Park-Test", "Open-Test")

    def status(self):
        if not self.on:
            w = {"enabled": False, "state": "off", "summary": "Wi-Fi: off"}
        elif self.conn:
            w = {"enabled": True, "state": "connected", "ssid": self.conn, "summary": "Wi-Fi: " + self.conn}
        else:
            w = {"enabled": True, "state": "disconnected", "summary": "Wi-Fi: on, not connected"}
        return {"ok": True, "wifi": w, "_t": self.t}

    def scan(self):
        nets = []
        if self.on:
            for n in self.near:
                nets.append({"ssid": n, "signal": self.rnd.randint(1, 99), "security": "open" if n == "Open-Test" else "WPA2",
                             "secure": n != "Open-Test", "active": n == self.conn, "known": n in self.saved,
                             "supported": True})
        return {"ok": True, "networks": nets, "saved": sorted(self.saved - set(self.near)), "_t": self.t}

    def apply(self, action, fields):
        """The change takes effect. True when it changed anything."""
        if action == "net_join":
            name = fields.get("ssid")
            if not self.on or not (name in self.saved or fields.get("password") or name == "Open-Test"):
                return False
            self.conn = name
            if name != "Open-Test":
                self.saved.add(name)
            return True
        if action == "net_forget":
            self.saved.discard(fields.get("ssid"))
            if self.conn == fields.get("ssid"):
                self.conn = None
            return True
        mode = fields.get("mode")
        if mode == "on":
            self.on = True
        elif mode == "off":
            self.on, self.conn = False, None
        else:
            self.conn = None
        return True

    def outside(self):
        """The network changes by itself, or from outside the page. True when it changed anything."""
        r, before = self.rnd.random(), (self.on, self.conn)
        if r < 0.5 and self.on:
            self.conn = self.rnd.choice(sorted(self.saved & set(self.near)) or [None])     # roaming, or joined elsewhere
        elif r < 0.65 and self.on:
            self.conn = None                    # dropped
        elif r < 0.8:
            self.on = not self.on               # switched outside the page
            if not self.on:
                self.conn = None
        elif self.on:
            self.conn = "Open-Test"             # an open network joined elsewhere
        return (self.on, self.conn) != before


class Fuzz(unittest.TestCase):
    """The page under random orderings, outcomes and taps; every invariant above holds for every seed."""

    def setUp(self):
        base.NetworkPage.setUp(self)        # the stand-in toolkit, threads that run only when told, a still clock
        self.counts = collections.Counter()     # over every seed: what the run did, so the checks are not vacuous

    # --- one run ----------------------------------------------------------------------------------------------------
    def fresh(self, seed):
        self.rnd = random.Random(seed)
        self.world = World(self.rnd)
        self.page = page = self.screens.Network(self.shell)
        page.now = lambda: self.clock[0]
        self.threads.clear()
        self.deliveries, self.late, self.effects, self.delivered = [], [], [], []
        self.last_effect, self.in_flight, self.typed_for, self.tapping = None, 0, None, None
        self.buttons, self.rows_sig, self.rows_at = {}, None, float("-inf")
        self.read, self.read_visit = None, None     # the radios read last seen drawn, and the visit it was drawn in
        self.violations, self.trace, self.outsides = [], [], 0
        self.asked, self.confirm_at = 0, None       # requests sent so far; the core's time at the confirming Disconnect tap
        self.rereading = False                      # a confirming tap's read is waiting
        self.screens.GLib.idle_add = lambda fn, *a: self.deliveries.append((fn, a)) or 0
        self.shell.core = types_core(self)
        real_send, real_done = page.send, page.done

        def send(action, fields, then):
            self.asked += 1
            asked = self.asked                  # the order the page sent its requests in, carried on each reply
            if action in page.CHANGES:
                self.at_change(action, dict(fields))
                self.in_flight += 1
                if self.in_flight > 1:
                    self.bad("two changes in flight", action)
            return real_send(action, fields, lambda got: then(dict(got, _asked=asked)))

        def done(got, visit=None):
            self.in_flight -= 1
            if self.last_effect is not None:
                self.delivered.append(self.last_effect)     # this change took effect before its answer came
            return real_done(got, visit)
        page.send, page.done = send, done
        self.shell.page = "network"
        page.enter()

    def bad(self, kind, *detail):
        self.violations.append((kind,) + detail)

    def core_net(self, action, fields, _timeout):
        """The stand-in core: reads answer from the world; a change gets a random outcome."""
        w = self.world
        w.t += 1
        if action == "net_status":
            return w.status()
        if action == "net_scan":
            return w.scan()
        out = self.rnd.choice(OUTCOMES)
        fields = dict(fields)
        self.trace.append(("core", action, sorted(k for k in fields if k != "password"), out))
        self.last_effect = None
        if out in ("ok", "late_effect", "core_slow_effect", "no_answer"):
            if w.apply(action, fields):
                self.effects.append(w.t)
                self.last_effect = w.t
        elif out == "dropped" and action == "net_join":
            w.conn = None                   # a failed join that dropped the old network
            self.effects.append(w.t)
            self.last_effect = w.t
        elif out in ("late", "core_slow"):
            self.late.append((action, fields))     # takes effect at a later step, after its answer
        if out in ("late", "late_effect"):
            raise TimeoutError("timed out")
        if out in ("core_slow", "core_slow_effect"):
            return {"ok": False, "error": CORE_SLOW}
        if out == "refused":
            return {"ok": False, "error": "Another network change is still running."}
        if out == "http429":
            raise urllib.error.HTTPError(base.URL, 429, "too many requests", {}, io.BytesIO(b"{}"))
        if out == "dropped":
            return {"ok": False, "error": "The password was not accepted."}
        if out == "no_answer" or self.last_effect is None:
            return {"ok": False, "error": "That did not go through."}
        return {"ok": True, "message": "Done."}

    # --- the checks -------------------------------------------------------------------------------------------------
    def at_change(self, action, fields):
        p = self.page
        read_at = (p.status_data or {}).get("_t", -1)
        newest = max(self.delivered, default=0)
        if action == "net_wifi":
            if p.status_data is not None and p.status_data is self.read and self.read_visit != p.visit:
                self.bad("a radios read from an earlier visit was trusted", fields.get("mode"), self.read_visit, p.visit)
            if read_at < newest:
                self.bad("a radios read from before a change was trusted", fields.get("mode"), read_at, newest)
            if fields.get("mode") == "disconnect" and self.world.conn != p.ssid() and \
                    all(e <= read_at for e in self.effects):
                self.bad("disconnect dropped another network", self.world.conn, p.ssid())
            if fields.get("mode") == "disconnect":
                self.counts["disconnects"] += 1
                if self.confirm_at is None or read_at <= self.confirm_at:
                    self.bad("disconnect was sent on a radios read made before the confirming tap", read_at, self.confirm_at)
                self.confirm_at = None
        if "password" in fields:
            if fields["password"] != PW:
                self.bad("a password other than the one typed was sent")
            if self.typed_for is None or fields.get("ssid") != self.typed_for:
                self.bad("a password was sent for a network it was not typed for", fields.get("ssid"), self.typed_for)
        if self.tapping in ("main", "wifi"):
            what, at, by_owner = self.buttons.get(self.tapping, (None, float("-inf"), True))
            if not by_owner and self.clock[0] < at + p.HOLD_S:     # the same sum the page makes (Network.hold)
                self.bad("a tap acted within HOLD_S of a change it did not make", self.tapping, what, action)

    def after_step(self, by_owner):
        p = self.page
        now = {"main": (p.main.get_label(), p.mode(), p.ssid(), p.main.visible),
               "wifi": (p.wifi.get_label(), p.wifi.visible and p.idle.visible)}
        for which, what in now.items():
            old = self.buttons.get(which)
            if old is None or old[0] != what:
                self.buttons[which] = (what, self.clock[0], by_owner)
        if p.status_data is not self.read:
            self.read, self.read_visit = p.status_data, p.visit
        sig = tuple((r["ssid"], netlogic.choice(r), r["visible"]) for r in netlogic.rows(p.scan_data))
        if p.scan_data is None and getattr(p.nets, "rowset", None) is not None:
            sig = ("reading",)                  # the list was forgotten on leaving: one row that chooses nothing
        if (p.scan_data is not None or sig == ("reading",)) and sig != self.rows_sig:
            self.rows_at = self.clock[0] if self.rows_sig is not None else float("-inf")
            self.rows_sig = sig
        if not len(p.secret):
            self.typed_for = None
        elif p.mode() not in ("password", "other") or self.shell.page != "network":
            self.bad("the typed password is held outside a typing pane", p.mode(), self.shell.page)
        elif self.typed_for is not None and (p.ssid() if p.mode() == "password" else p.ssid_entry.text) != self.typed_for:
            self.bad("the typed password now belongs to another network", p.ssid() or p.ssid_entry.text, self.typed_for)
        known, now = netlogic.connected_to(p.status_data)
        if known and p.chosen and not p.other and p.scan_data is not None:
            read, listed = p.status_data.get("_asked", 0), p.scan_data.get("_asked", 0)
            row = next((x for x in netlogic.rows(p.scan_data) if x["ssid"] == p.ssid()), None)
            if read > listed and bool(p.chosen.get("active")) != (now == p.ssid()):
                self.bad("the chosen row follows a list asked before the radios read drawn", p.ssid(), now, listed, read)
            elif listed > read and row is not None and bool(p.chosen.get("active")) != row["active"]:
                self.bad("the chosen row follows a radios read asked before the list drawn", p.ssid(), now, read, listed)

    # --- the events -------------------------------------------------------------------------------------------------
    def step(self):
        p, r = self.page, self.rnd
        showing = self.shell.page == "network"
        acts = ["deliver"] * 5 + ["process"] * 5 + ["look"] * 3 + ["tick", "outside"] + (["late"] if self.late else [])
        if showing:
            acts += ["row"] * 4 + ["main"] * 5 + ["wifi"] * 2 + ["type"] * 2 + ["forget", "cancel", "return", "poll", "leave",
                                                                                "other", "edit"]
            if p.mode() == "connected":
                acts += ["main"] * 4            # Disconnect and its confirming tap are reached often,
            if getattr(p, "checking", None):
                acts += ["process", "deliver", "outside"] * 3   # and so are its read's answer and a roam before it
            if p.mode() == "other":
                # a hidden network gets a name, a password, then an edited name; and the pane is left as often as before
                acts += ["type"] * 2 + ["edit"] * 2 + ["cancel", "return"]
            else:
                acts += ["connected"] * 2       # the owner goes for the row the list says is connected
        else:
            acts += ["enter"] * 3 + ["outside"] * 2      # while the page is left, the network is changed elsewhere more often
        a = r.choice(acts)
        self.tapping = None
        owner = a in ("row", "connected", "main", "wifi", "forget", "cancel", "type", "return", "leave", "other", "edit")
        if a == "deliver" and self.deliveries:
            fn, args = self.deliveries.pop(r.randrange(len(self.deliveries)))
            fn(*args)
        elif a == "process" and self.threads:
            self.threads.pop(r.randrange(len(self.threads)))()
        elif a == "late":
            self.world.t += 1
            if self.world.apply(*self.late.pop(0)):
                self.effects.append(self.world.t)
        elif a == "outside":
            self.world.t += 1
            if self.world.outside():
                self.effects.append(self.world.t)
                self.outsides += 1
        elif a == "look":
            self.clock[0] += p.HOLD_S
        elif a == "tick":
            self.clock[0] += 0.2
        elif a in ("row", "connected"):
            rows = getattr(p.nets, "rowset", None)
            if rows and rows.words:
                before = p.chosen
                place = r.randrange(len(rows.words))
                if a == "connected":
                    place = next((i for i, x in enumerate(netlogic.rows(p.scan_data)) if x["active"]), place)
                rows.click(place)
                if p.chosen is not before and p.chosen is not None and self.clock[0] < self.rows_at + p.HOLD_S:
                    self.bad("a row tap chose a network within HOLD_S of the list changing", p.ssid())
        elif a == "main":
            self.tapping = "main"
            confirming = p.main.get_label() == "Disconnect now?" and not p.busy
            if confirming:
                self.confirm_at = self.world.t      # a read made up to now is from before this tap
            p.main_action()
            if confirming and getattr(p, "checking", None):
                self.rereading = True
                # the tap was taken (the page now reads the radios again before it sends anything): it must have been
                # an informed one, by the same rule as a tap that sends at once (at_change)
                self.counts["rereads"] += 1
                what, at, by_owner = self.buttons.get("main", (None, float("-inf"), True))
                if not by_owner and self.clock[0] < at + p.HOLD_S:
                    self.bad("a confirming tap was taken within HOLD_S of a change it did not make", what)
        elif a == "wifi" and p.idle.visible:
            self.tapping = "wifi"
            p.toggle_wifi()
        elif a == "forget" and p.forget_b.visible and p.chips.visible:
            p.forget()
        elif a == "cancel" and p.chips.visible:
            p.cancel()
        elif a == "type" and p.keys.visible:
            self.type_something()
        elif a == "return" and p.keys.visible:
            p.key("\n")
        elif a == "other" and p.idle.visible:
            p.other_network()
        elif a == "edit" and p.mode() == "other":
            had, before = len(p.secret), p.ssid_entry.text      # the owner goes back to the name and changes it
            p.focus("name")
            p.key(None if r.random() < 0.5 else "x")
            if had and p.ssid_entry.text != before:
                self.counts["edits"] += 1
        elif a == "poll":
            p.poll()
        elif a == "leave":
            self.shell.page = "home"
            p.leave()
        elif a == "enter":
            self.shell.page = "network"
            p.enter()
        self.trace.append((a, p.mode(), p.ssid(), p.main.get_label(), p.wifi.get_label(), p.busy))
        if self.rereading and not getattr(p, "checking", None):
            self.rereading = False
            if a == "deliver" and self.in_flight == 0:
                self.counts["refused"] += 1     # the read came and the page sent nothing
        self.after_step(owner)

    def type_something(self):
        p = self.page
        if p.mode() == "other" and p.field == "name":
            for ch in HIDDEN_NAME:
                p.key(ch)
            p.key("\n")
        elif p.mode() in ("password", "other") and not len(p.secret):
            for ch in PW:
                p.key(ch)
            if len(p.secret) == len(PW):
                self.typed_for = p.ssid() if p.mode() == "password" else p.ssid_entry.text

    # --- the test ---------------------------------------------------------------------------------------------------
    def test_no_seed_breaks_an_invariant(self):
        first, outcomes, sent, outsides = {}, set(), set(), 0
        for seed in range(SEEDS):
            self.fresh(seed)
            try:
                for _ in range(STEPS):
                    self.step()
            except Exception as err:        # a crash is a finding too
                self.bad("the page raised", type(err).__name__, str(err)[:120])
            for v in self.violations:
                first.setdefault(v[0], (seed, v, self.trace[-12:]))
            outsides += self.outsides
            outcomes |= {t[3] for t in self.trace if t[0] == "core"}
            sent |= {t[1] for t in self.trace if t[0] == "core"}
        report = "\n".join("%s (seed %d): %r\n  last steps: %r" % (kind, seed, v, tail)
                           for kind, (seed, v, tail) in sorted(first.items()))
        self.assertEqual(sorted(first), [], report)
        # and it is not vacuous: every kind of change was sent, and the core gave every outcome
        self.assertEqual(sent, {"net_join", "net_forget", "net_wifi"})
        self.assertEqual(outcomes, set(OUTCOMES))
        self.assertGreater(outsides, SEEDS, "the network changed outside the page")
        self.assertGreater(self.counts["edits"], SEEDS // 20, "a hidden network's name was edited after its password was typed")
        self.assertGreater(self.counts["rereads"], SEEDS // 10, "a confirming Disconnect tap was taken")
        self.assertGreater(self.counts["refused"], SEEDS // 100, "its read named another network or none, so nothing was sent")
        self.assertGreater(self.counts["disconnects"], SEEDS // 50, "and a Disconnect was sent after its read")


def types_core(test):
    """A core with the one method the page calls."""
    class Core:
        def net(self, action, fields, timeout):
            return test.core_net(action, fields, timeout)
    return Core()


if __name__ == "__main__":
    unittest.main()
