# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import http.client
import json
import threading
import time
import urllib.error
import opos.assist as assist
import opos.muse as muse
from opos.governor import Decision, Mode, Priority
GOAL_BUCKET_H = 0.5  # the goal horizon shrinks every tick; advice is reused within this bucket
def goal_bucket(goal_hours):
    if goal_hours is None:
        return None
    return round(float(goal_hours) / GOAL_BUCKET_H) * GOAL_BUCKET_H
def _parse_defer(reply):
    """Find the last JSON object with a list-valued defer field; objects nested inside it do not count."""
    decoder = json.JSONDecoder()
    defer = None
    pos = reply.find("{")
    while pos != -1:
        nxt = pos + 1
        with contextlib.suppress(ValueError):
            data, end = decoder.raw_decode(reply, pos)
            if isinstance(data, dict) and isinstance(data.get("defer"), list):
                defer, nxt = data["defer"], end
        pos = reply.find("{", nxt)
    if defer is None:
        raise ValueError("no defer list")
    return defer
class GlimmerAdvisor:
    def __init__(self, base, route=None, ask=None, spawn=None, clock=None, backoff=60.0):
        self.base = base
        self.route = route if route is not None else assist.explain_route()
        self.ask = ask if ask is not None else muse.ask
        self.spawn = spawn if spawn is not None else lambda fn: threading.Thread(target=fn, daemon=True).start()
        self.clock = clock if clock is not None else time.monotonic
        self.backoff = backoff
        self._lock = threading.Lock()
        self.cache = {}
        self.pending = set()
        self.down_until = 0.0
        self.failure = "no reply"
    @property
    def visible(self):
        return self.base.visible
    @visible.setter
    def visible(self, v):
        self.base.visible = v
    def status(self):
        """Observed advisor state, not a probe of the configured helper route."""
        with self._lock:
            if self.clock() < self.down_until:
                return self.failure
            return "waiting" if self.pending else "ready"
    def __call__(self, state, tasks):
        d = self.base(state, tasks)
        if not self.base.visible:
            return d
        m = {t.name: t for t in tasks}
        noness = [n for n in d.allowed if n in m and m[n].priority != Priority.ESSENTIAL]
        if not noness:
            return d
        key = (round(state.level / 10), bool(state.charging), round(state.health, 1), round(state.temp_c / 5), goal_bucket(state.goal_hours), tuple(noness))
        with self._lock:
            if key in self.cache:
                return self._apply(d, tasks, self.cache[key])
            # At most one inference in flight across all keys, so a running simulation cannot queue
            # a Glimmer request per tick ahead of the owner's Why, Plan and Ask.
            if self.pending or self.clock() < self.down_until:
                return d
            self.pending.add(key)
        def do():
            try:
                prompt = f"You help a phone save battery. State: battery {state.level} percent, health {state.health}, {state.temp_c} C, charging {state.charging}, goal hours {state.goal_hours if state.goal_hours is not None else 'none'}. These optional tasks are currently allowed: {', '.join(noness)}. Reply with only JSON like {{\"defer\": [names]}} listing which of them to pause to protect calls, messages and alarms. Use an empty list if all are fine."
                reply = self.ask(self.route, prompt, timeout=120, max_tokens=60)
                raw = _parse_defer(reply)
                filt = tuple(n for n in raw if isinstance(n, str) and n in noness)
                with self._lock:
                    if len(self.cache) >= 64:
                        self.cache.clear()
                    self.cache[key] = filt
            except (muse.MuseError, ValueError, KeyError, TypeError, AttributeError, RecursionError, http.client.HTTPException) as e:
                with self._lock:
                    cause = e.__cause__
                    unreachable = isinstance(cause, (ConnectionError, urllib.error.URLError)) and not isinstance(cause, urllib.error.HTTPError)
                    self.failure = "not reachable" if unreachable else "no reply"
                    self.down_until = self.clock() + self.backoff
            finally:
                with self._lock:
                    self.pending.discard(key)
        try:
            self.spawn(do)
        except Exception:
            with self._lock:
                self.pending.discard(key)
            raise
        return d
    def _apply(self, d, tasks, defer):
        if not defer:
            return d
        m = {t.name: t for t in tasks}
        essential = {n for n, t in m.items() if t.priority == Priority.ESSENTIAL}
        defer_set = set(defer) - essential
        defer_set = defer_set.intersection(d.allowed)
        if not defer_set:
            return d
        allowed = tuple(n for n in d.allowed if n not in defer_set)
        moved = tuple(n for n in d.allowed if n in defer_set)
        deferred = d.deferred + moved
        all_essential = True
        for n in allowed:
            if n in m and m[n].priority != Priority.ESSENTIAL:
                all_essential = False
                break
        mode = Mode.CRITICAL if all_essential else Mode.SAVER
        return Decision(mode, allowed, deferred, "router+glimmer")
