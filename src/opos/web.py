# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import os
import http
import html
from html import escape as html_escape
import json
import logging
import math
import re
import secrets
from pathlib import Path
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import opos.muse as muse
import opos.assist as assist
from opos.advisor import GlimmerAdvisor
import opos.setup.config as setup_config
from opos.setup import prefs


class _Server(ThreadingHTTPServer):
    """Bounded-connection threading server."""
    daemon_threads = True
    request_queue_size = 16
    MAX_CONNS = 32
    def __init__(self, *args, **kwargs):
        """Create server with connection semaphore."""
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(type(self).MAX_CONNS)
    def process_request(self, request, client_address):
        """Admit connection or close when full."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)
    def process_request_thread(self, request, client_address):
        """Serve connection then release slot."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
from opos.contacts import DEMO_CONTACTS, Book, thread, threads
from opos.essentials import Essentials, normalize_number, normalize_sender
from opos.runtime import Runtime
from opos.sim import DrainSim
from opos.ui import ScreenModem
from opos.dashboard import ROUGH, Dashboard, _format_goal_12h
from opos.router import HOT_C
from opos import notices, practice, secure, tokenfile, update_client, update_page
from opos.span import apply as apply_span, parse as parse_span
try:  # track osk: the on-screen keyboard is optional; a runtime shipped without src/opos/osk.py serves what it always served
    from opos import osk
except ImportError:
    osk = None
from opos import net_page  # track: network
from opos import apps_page  # everyday apps: /apps and /api/<app>; the page is unchanged unless OPOS_APPS names one
from opos import lockpin  # optional PIN. Off unless OPOS_LOCK=1, so the demo is unchanged.
try:  # track monitor: the Inference page is optional; a runtime shipped without src/opos/monitor.py serves what it always served
    from opos import monitor, monitor_web
except ImportError:
    monitor = monitor_web = None
from opos import statusbar  # track: status (the status bar's note, the call line on Home)


def self_rss_mb(path="/proc/self/status"):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _restart_service():
    t = threading.Timer(1.0, os._exit, (1,))
    t.daemon = True
    t.start()


def _route_with_glimmer(phone, env):
    if env.get("OPOS_GLIMMER_ROUTING") != "0":
        gov = phone.runtime.governor
        gov.advisor = GlimmerAdvisor(gov.advisor, route=assist.explain_route())
    return None


_NOTICE_FORBIDDEN_RE = re.compile(r'\b(AI|model|Governor|assistant|battery)\b', re.IGNORECASE)
_EMPTY_NOTICES = {
    "error: battery empty, charge the phone to call": "Charge the phone to call",
    "error: battery empty, charge the phone to send": "Charge the phone to send",
    "call ended: battery empty": "Call ended. Charge the phone",
}
def notice_for(status, in_call, sim=False):
    if status in _EMPTY_NOTICES:
        return _EMPTY_NOTICES[status]
    if isinstance(status, str) and status.startswith("error: "):
        rest = status[len("error: "):]
        low = rest.lower()
        if "battery" in low or "temp" in low or "speed" in low:
            return ""
        if not rest:
            return ""
        out = rest[0].upper() + rest[1:] if len(rest) > 1 else rest.upper()
        if _NOTICE_FORBIDDEN_RE.search(out):
            return ""
        return out
    if (p := practice.notice(status, in_call, sim)):  # a practice phone says so: no real call, no real text
        return p
    if in_call:
        return "In call"
    if status == "call ended":
        return "Call ended"
    if status == "sent":
        return "Message sent"
    if status == "message received":
        return "New message"
    return ""
MAX_TICK_S = 5.0  # the longest real interval one simulation tick covers
def poll_ms(mode, free, in_call):
    if in_call:
        return 1000
    if mode == "CRITICAL":
        return 5000
    if mode == "SAVER" or (free is not None and free < 1536):
        return 3000
    return 1000
def _num(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("bad number")
    try:
        v = float(v)
    except (OverflowError, ValueError, TypeError):
        raise ValueError("bad number")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError("bad number")
    return v
def _join(names):
    names = [str(n) for n in names]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
# Owner-facing words call a battery "worn" below 80 percent health. The worn-battery
# reserve (opos.reserve) only engages below opos.eol.WORN_HEALTH (50 percent).
WORN_WORDS_HEALTH = 0.8
def lock_story(level, health, temp_c, charging, paused, goal=None, goal_met=True, *, health_known=True):
    """A short lock-screen fact, using effective pauses rather than helper visibility. A health nobody read is not said."""
    prefix = ("Charging. " if charging else "Phone hot. " if temp_c >= HOT_C else
              "Battery low. " if level < 20 else "Worn battery. " if health_known and health < WORN_WORDS_HEALTH else "")
    names = sorted(set(paused))
    suffix = ""
    if goal and not charging:
        goal_words = _format_goal_12h(goal)
        if not goal_met:
            suffix = f"; {goal_words} unlikely"
        elif names:
            suffix = f" for {goal_words}"
    action = _join(names).capitalize() + " paused" if names else "Nothing paused"
    if len(prefix + action + suffix) > 48 and names:
        action = f"{len(names)} task{'s' if len(names) != 1 else ''} paused"
    return prefix + action + suffix + "."
def why_paused(level, health, temp_c, charging, paused, goal=None, goal_met=True, time_left=None, *, usable_percent=None, health_known=True):
    """One plain sentence, from the numbers alone, that the helper repeats for why-questions.

    Health is said only when it was read and is under 80 percent; the time left is said without its "Rough estimate" label.
    """
    pct, hp = int(round(level)), int(round(health * 100))
    usable = int(level * health) if usable_percent is None else usable_percent
    if time_left:
        time_left = time_left.removeprefix(ROUGH)
    if health_known and health < WORN_WORDS_HEALTH:
        s = f"The battery is worn. It holds {hp}% of what it did when new, so {pct}% is really about {usable}%"
    else:
        s = f"The battery is at {pct}%"
    if temp_c >= HOT_C:
        s += f", the phone is hot ({temp_c:.0f}°C)"
    s += " and it is charging" if charging else " and it is not charging"
    if paused:
        if goal and goal_met is False:
            if time_left and not charging:
                return s + f"; to stretch the battery toward {goal}, the phone paused " + _join(paused) + f", but at this rate the battery shows {time_left.lower()}."
            else:
                return s + f"; to stretch the battery toward {goal}, the phone paused " + _join(paused) + "."
        until = f" until {goal}" if goal else ""
        return s + f"; to keep calls and messages working{until}, the phone paused " + _join(paused) + "."
    if goal and goal_met is False and not charging:
        s += f"; nothing is paused, but the battery may not last until {goal}"
        if time_left:
            s += f"; at this rate it shows {time_left.lower()}"
        return s + "."
    return s + "; nothing is paused, so everything is on."
def _contact(c):
    if isinstance(c, dict):
        return {"name": str(c.get("name", "")), "number": str(c.get("number", ""))}
    if isinstance(c, (list, tuple)):
        return {"name": str(c[0]), "number": str(c[1])}
    return {"name": str(c.name), "number": str(c.number)}
def _local_clock_s():
    t = time.localtime()
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
CLOCK_MIN_YEAR = 2026  # an earlier year is a clock nobody set: the Duo boots at 1970
CLOCK_GUESS = "/run/opos/clock-floor"  # the OS image's opos-clock writes it when it raised an unset clock to its floor
CLOCK_SYNCED = "/run/systemd/timesync/synchronized"  # systemd-timesyncd touches it once it has network time
def clock_ok(now=None, guess=CLOCK_GUESS, synced=CLOCK_SYNCED):
    """False while the wall clock is a guess: before 2026, or only raised to the image's floor and not set since."""
    if time.gmtime(time.time() if now is None else now).tm_year < CLOCK_MIN_YEAR:
        return False
    return not os.path.exists(guess) or os.path.exists(synced)
class Phone:
    def __init__(self, visible=False, live_battery=None):
        self.visible = bool(visible)
        self.runtime = Runtime(visible=self.visible)
        self.modem = ScreenModem()
        self.essentials = Essentials(self.modem)
        self.book = Book(DEMO_CONTACTS)
        # live_battery: a LiveBattery reading the phone's own battery and wall clock in place of the demo's noon simulation.
        self.live = live_battery is not None
        self.sim = live_battery if self.live else DrainSim(60.0, health=0.6, temp_c=30.0, speed=1.0)  # Saver at start: the helper answers, maps and photos backup rest
        self.dash = Dashboard(self.runtime.tasks)
        if self.live:
            self.runtime.estimator = self.sim.rate_estimator()  # reserve and hours_left follow measured discharge, not raw level steps
        self.clock_s = _local_clock_s() if self.live else 43200
        self.clock_ok = clock_ok() if self.live else True  # the demo's clock is its own simulation
        self.charging = bool(self.sim.charging) if self.live else False
        self.running = False
        self.open_number = None
        self.status = "ready"
        self.status_app = ""
        self.dash.record(self.sim.level, self.clock_s)
        self._in_call = False
        self._call_with = ""  # who the call is with (a contact's name, else the number), for the Home line
        self._tick_obj = None
        self._undo_mode = None  # the mode an Undo was made in; a different mode ends it
        self._last_tick_time = time.monotonic()
    def _tick(self):
        """The one final decision: home screen, battery panel, lock story, helper facts and drain read it.

        The runtime applies the owner's Undo and foreground app over soft pauses, then the hard
        floors. An Undo lasts until the mode changes.
        """
        if self.live:
            self.sim.refresh()
            self.charging = bool(self.sim.charging)
            self.clock_s = _local_clock_s()
            self.dash.record(self.sim.level, self.clock_s)  # the last-hour graph grows while the phone is read, not only when the demo ticks
            was_ok, self.clock_ok = self.clock_ok, clock_ok()
            if self.clock_ok and not was_ok and self.dash.goal:
                # A goal set against a guessed clock counts as set before its time. The clock is real now, so
                # judge the goal again as if it were set just now: inside its grace window it means the next one.
                self.dash.set_goal(self.dash.goal, self.clock_s)
        hours = self.dash.hours_to_goal(self._goal_clock())
        def step(undone):
            return self.runtime.step(self.sim.t_s, self.sim.level, health=self.sim.health, temp_c=self.sim.temp_c,
                                     charging=self.charging, goal_hours=hours, in_use=self.dash.in_use, undone=undone)
        tick = step(frozenset(self.dash.undone))
        if not self.dash.undone:
            self._undo_mode = None
        elif self._undo_mode is None:
            self._undo_mode = tick.decision.mode
        elif tick.decision.mode != self._undo_mode:
            self.dash.undone.clear()
            self._undo_mode = None
            tick = step(frozenset())
        self._tick_obj = tick
        return tick
    def _goal_clock(self):
        """The time of day goals may read: None while the live clock is a guess, so no goal or arrival time rests on it."""
        return self.clock_s if self.clock_ok else None
    def undo(self, name):
        """Turn a paused task back on, unless a hard floor holds it (then Undo has no effect)."""
        real_name = self.dash.task_name(name)
        if real_name in self._tick().held:
            return
        self.dash.undo(real_name)
    def paused_names(self):
        return set(self.dash.paused_names)
    def _goal_label(self):
        """The goal still ahead as HH:MM, plus " tomorrow" past midnight; None without one."""
        hours = self.dash.hours_to_goal(self._goal_clock())
        if hours is None:
            return None
        return self.dash.goal + (" tomorrow" if self.clock_s + hours * 3600 >= 86400 else "")
    def _paused_reasons(self, tick, *, goal_met=True):
        """One sentence per paused task, from the cause the final decision recorded for it."""
        goal_label = self._goal_label()
        goal_words = _format_goal_12h(goal_label) if goal_label else None
        save = "to save power for calls and messages"
        why = {
            "reserve": "to keep a battery reserve for calls",
            "hot": "because the phone is hot",
            "low": "because usable battery is low" if self.visible else "because the battery is low",
            "goal": (f"to help the battery last until {goal_words}" if goal_met else
                     f"because the battery won't last until {goal_words}") if goal_words else save,
            "optional": "because usable battery is below 80%" if self.visible else save,
            "horizon": "to keep power for the next 4 hours",
            "rules": "because usable battery is low" if self.visible else save,
        }
        return {name: f"{'Muse Glimmer' if cause == 'glimmer' else 'Battery rules'} paused this {why.get(cause, save)}."
                for name, cause in tick.causes}
    def set_visible(self, visible):
        self.visible = bool(visible)
        try:
            self.runtime.visible = self.visible
        except AttributeError:
            pass
        governor = getattr(self.runtime, 'governor', None)
        advisor = getattr(governor, 'advisor', None) if governor else None
        if advisor is not None and hasattr(advisor, 'visible'):
            advisor.visible = self.visible
    def state(self):
        tick = self._tick()
        d = tick.decision
        reached = self.dash.goal_reached(self._goal_clock())
        health_known = getattr(self.sim, "health_source", None) != "assumed"  # a health nobody read is not said
        out = {"screen": [html.escape(str(s)) for s in tick.screen], "brightness": int(tick.budget.screen_brightness), "status": self.status, "in_call": self._in_call, "notice": notice_for(self.status, self._in_call, practice.simulated(self.essentials)), "helper_visible": self.visible, "running": self.running, "contacts": [_contact(c) for c in DEMO_CONTACTS], "threads": threads(self.book, self.essentials.log), "thread": thread(self.book, self.essentials.log, self.open_number) if self.open_number else []}
        out["notice_app"] = "phone" if self._in_call and not self.status.startswith("error:") else self.status_app
        # Only the active, known in-memory modem is identified as simulated.
        out["simulation"] = {"modem": practice.simulated(self.essentials)}
        if self.live:
            out["battery_source"] = {"source": self.sim.source, "health_source": self.sim.health_source}
        out["prefs"] = self._preferences()
        out["temp_c"] = round(float(self.sim.temp_c), 1)
        out["charging"] = bool(self.charging)
        free, rss, engine = self._environment()
        if self.visible:
            out["helper"] = {"mode": d.mode.name, "allowed": list(d.allowed), "paused": list(d.deferred), "hours_left": None if math.isinf(tick.hours_left) else round(tick.hours_left, 1), "reserve": round(tick.reserve, 1), "source": "router" if d.reason.startswith("router") else "rules", "engine": engine}
        drain, drain_info = tick.drain_per_h, None
        if self.live:
            drain, drain_info = self.sim.drain_rate(drain)  # the phone's measured discharge once trusted, else the model's
        out["battery"] = self.dash.view(self.sim.level, d.mode, self.charging, self.sim.health, self._goal_clock(), self.visible, d.reason,
                                        deferred=d.deferred, steps=tick.steps, drain_per_h=drain,
                                        measured=bool(drain_info and drain_info["basis"] == "measured"))
        if self.live:
            out["battery"]["clock_ok"] = self.clock_ok
            if not self.clock_ok and self.dash.goal:
                out["battery"]["goal_note"] = "Clock not set, so the goal time is not used"
        if drain_info is not None:
            out["battery"]["drain"] = drain_info
            if "why" in out["battery"] and not self.charging:
                out["battery"]["why"] += "; " + self.sim.drain_words(drain_info)
        reasons = self._paused_reasons(tick, goal_met=out["battery"]["goal_met"])
        held = set(tick.held)
        for item in out["battery"]["paused"]:
            name = self.runtime.tasks[int(item["id"][1:])].name
            item["reason"] = reasons[name]
            item["held"] = name in held  # a hard floor holds it: the page shows no Undo
        goal_label = self._goal_label()
        out["lock_story"] = lock_story(self.sim.level, self.sim.health, self.sim.temp_c, self.charging, d.deferred, goal_label, out["battery"]["goal_met"], health_known=health_known)
        if self.visible and "helper" in out:
            hf = out["helper"]
            advisor = self.runtime.governor.advisor
            hf["glimmer_status"] = advisor.status() if isinstance(advisor, GlimmerAdvisor) else "off"
            hf["battery_percent"] = int(round(self.sim.level))
            if health_known:
                hf["battery_health_percent"] = int(round(self.sim.health * 100))
            hf["usable_percent"] = int(self.sim.level * self.sim.health)  # floor: 14.7% usable must not read as 15
            hf["temp_c"] = out["temp_c"]
            hf["charging"] = out["charging"]
            hf["goal"] = self.dash.goal if self.clock_ok else ""
            hf["goal_met"] = bool(out["battery"]["goal_met"])
            hf["time_left"] = out["battery"]["time_left"]
            hf["why_paused"] = why_paused(self.sim.level, self.sim.health, self.sim.temp_c, self.charging, hf["paused"], goal_label, goal_met=hf["goal_met"], time_left=hf["time_left"], usable_percent=hf["usable_percent"], health_known=health_known)
            if reached and hf["goal_met"]:
                # After the goal, goal_met must not read as "lasts until the goal time".
                hf["why_paused"] = hf["why_paused"][:-1] + f"; the {_format_goal_12h(self.dash.goal)} goal time has passed and calls and messages stay on."
            glimmer = [n for n, cause in tick.causes if cause == "glimmer"]
            if glimmer:
                hf["paused_by_glimmer"] = glimmer
        out["home_note"] = practice.home_note(out["simulation"]["modem"], out["battery"]["paused"])  # the Home card, worded once here
        h = int(self.clock_s // 3600) % 24
        m = int((self.clock_s % 3600) // 60)
        out["clock"] = f"{h:02d}:{m:02d}" if self.clock_ok else "--:--"
        out["lean"] = {"opos_mb": rss, "free_mb": free}
        out["poll_ms"] = poll_ms(d.mode.name, free, self._in_call)
        statusbar.apply(self, out)  # track: status
        return out
    def _preferences(self):
        """Host preferences for the local UI; isolated simulations override this seam."""
        # Public install defaults only; never return setup answers or network names.
        public_prefs = prefs.defaults()
        try:
            saved = prefs.load()
            effective = prefs.effective(saved) if saved["applied"] else public_prefs
            if isinstance(effective, dict):
                for key in public_prefs:
                    if key in ("wifi", "pin"):
                        continue  # Record-only steps (network names, lock PIN) stay private; the kiosk sees the defaults.
                    try:
                        value = effective.get(key, public_prefs[key])
                        prefs.validate_answer(key, value)
                        public_prefs[key] = value
                    except (ValueError, TypeError):
                        pass
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return public_prefs

    def _environment(self):
        """Local diagnostics and helper label, separate from the simulated state."""
        free = assist.free_mb()
        return free, self_rss_mb(), muse.label(assist.ask_route(free=free)) if self.visible else ""

    def _helper_request(self):
        """Facts, saving steps and the ordered routes (Spark Open first when the goal is hard)."""
        s = self.state()
        h = s["helper"]
        goal = self.dash.hours_to_goal(self._goal_clock())
        hard = muse.choose(muse.is_hard(goal, h["hours_left"]))
        routes = assist.explain_routes(free=assist.free_mb())
        if hard.key == "spark-open":
            routes.insert(0, hard)
        return muse.facts(h), list(s["battery"]["steps"]), routes
    def explain_request(self):
        if not self.visible:
            return None
        f, _, routes = self._helper_request()
        return (f, routes[0])
    def explain_routes(self):
        if not self.visible:
            return None
        f, _, routes = self._helper_request()
        return (f, routes)
    def ask_facts(self):
        if not self.visible:
            return {}
        return muse.facts(self.state()["helper"])
    def plan_request(self):
        if not self.visible:
            return None
        f, steps, routes = self._helper_request()
        return (f, steps, routes[0])
    def plan_routes(self):
        if not self.visible:
            return None
        return self._helper_request()
    def set_battery(self, level):
        try:
            v = _num(level, 0, 100)
        except ValueError:
            self.status = "error: battery not valid"
            return
        self.dash.record(self.sim.level, self.clock_s)
        changed = self.sim.level != v
        self.sim.level = v
        self.status = "ready"
        self.dash.record(self.sim.level, self.clock_s, changed=changed)
        self._end_empty_call()
    def set_temp(self, t):
        try:
            v = _num(t, 15, 60)
        except ValueError:
            self.status = "error: temp not valid"
            return
        self.sim.temp_c = v
        self.status = "ready"
    def call(self, number):
        self.status_app = "phone"
        if practice.simulated(self.essentials) and practice.refuses(number):  # the practice phone must not pretend to reach 911 or 112
            self.status = practice.REFUSED
            return
        if round(self.sim.level) <= 0:
            self.status = "error: battery empty, charge the phone to call"
            return
        if self._in_call:
            self.status = "error: already in a call"
            return
        try:
            self._call_with = self.book.name_for(self.essentials.call(number))
            self._in_call = True
            self.status = "in call"
        except ValueError:
            self.status = "error: number not valid"
        except RuntimeError:
            self.status = "error: already in a call"
    def hang_up(self):
        self.status_app = "phone"
        try:
            self.essentials.end_call()
            self._in_call = False
            self.status = "call ended"
        except RuntimeError:
            self.status = "error: not in a call"
        except ValueError:
            self.status = "error: number not valid"
    def send(self, number, text):
        self.status_app = "messages"
        if round(self.sim.level) <= 0:
            self.status = "error: battery empty, charge the phone to send"
            return
        try:
            number = normalize_number(number)
        except ValueError:
            self.status = "error: number not valid"
            return
        try:
            self.essentials.send_message(number, text)
            self.status = "sent"
        except ValueError:
            self.status = "error: message empty or too long"
        except RuntimeError:
            self.status = "error: message empty or too long"
    def receive(self, number, text):
        self.status_app = "messages"
        try:
            number = normalize_sender(number)
        except ValueError:
            self.status = "error: number not valid"
            return
        try:
            self.essentials.receive_message(number, text)
            self.status = "message received"
        except ValueError:
            self.status = "error: message empty or too long"
        except RuntimeError:
            self.status = "error: message empty or too long"
    def open(self, number):
        self.status_app = "messages"
        self.open_number = str(number)
        self.status = "thread open"
    def set_sim(self, running, speed=None):
        old_running = self.running
        self.running = bool(running)
        if self.running and not old_running:
            self._last_tick_time = time.monotonic()
        if speed is not None:
            try:
                v = _num(speed, 0.001, 1e9)
            except ValueError:
                self.status = "error: speed not valid"
                return
            try:
                self.sim.speed = v
            except AttributeError:
                pass
        self.status = "simulation running" if self.running else "simulation paused"
    def tick(self, real_s=1):
        """Advance the simulation by real_s seconds of real time (times the speed).

        real_s is how long the page waited since its last tick. It is capped at MAX_TICK_S and
        at the real time since the previous tick, so two open pages do not double the speed.
        The battery drains at the final decision's rate, the same one the time-left estimate uses.
        """
        if isinstance(real_s, bool) or not isinstance(real_s, (int, float)):
            raise ValueError("bad real_s")
        try:
            real_s = float(real_s)
        except OverflowError:
            raise ValueError("bad real_s")
        if not math.isfinite(real_s) or real_s < 0:
            raise ValueError("bad real_s")
        now = time.monotonic()
        since = max(0.0, now - self._last_tick_time)
        self._last_tick_time = now
        dt = min(real_s, since, MAX_TICK_S)
        tick_obj = self._tick()
        speed = self.sim.speed
        self.clock_s = (self.clock_s + dt * speed) % 86400
        if self.charging:
            self.sim.charge(dt)
        else:
            # 60 simulated seconds matches tick_s=60 in opos.profiles, evaluate and evaleol.
            n = min(120, max(1, math.ceil(dt * speed / 60.0)))
            for _ in range(n):
                self.sim.advance(dt / n, tick_obj.decision.mode.name, (), per_hour=tick_obj.drain_per_h)
        self.dash.record(self.sim.level, self.clock_s)
        self._tick()
        if self.status != "call ended: battery empty" or self.charging or round(self.sim.level) > 0:
            self.status = "simulation running" if self.running else "ready"
        self._end_empty_call()
    def _end_empty_call(self):
        if self._in_call and not self.charging and round(self.sim.level) <= 0:
            self._in_call = False
            try:
                self.essentials.end_call()
            except RuntimeError:
                pass
            self.status_app = "phone"
            self.status = "call ended: battery empty"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
def _validate_token(token):
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("bad token")
    return token
_STATIC = Path(__file__).with_name("web_static")
def _asset(name):
    return (_STATIC / name).read_text(encoding="utf-8")
def _inline_assets(html):
    return re.sub(r"@@OPOS:([A-Za-z0-9_.-]+)@@", lambda m: _asset(m.group(1)), html)

def _about_notices():
    """Escaped shared text; readable even when the optional UI script fails."""
    return ('<div id="about-notices"><p id="about-disclaimer">'
            + html_escape(notices.DISCLAIMER)
            + '</p><details id="about-notices-details">'
            '<summary id="about-notices-summary" style="min-height:48px">'
            'License, privacy and tested limits</summary>'
            '<pre id="about-notices-text" style="white-space:pre-wrap;'
            'overflow-wrap:anywhere;font:inherit">'
            + html_escape(notices.as_text()) + '</pre></details></div>')

FAVICON = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff610000002a49444154789c63f8fae1c97f4a3003ba808888105e4c9401b86c1b3560d400fa1a30f049992217908a01dc7a527c28c3ed9a0000000049454e44ae426082")

def page(token="", nonce="", *, demo=False, span=None):
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Old Phone OS Interface</title><link rel="icon" href="/favicon.ico" type="image/png"><style>
@@OPOS:css-1.css@@
@@OPOS:css-2.css@@
@@OPOS:css-3.css@@
@@OPOS:css-4.css@@
.box{border-radius:8px;padding:8px;margin:6px 12px;white-space:pre-wrap}
#upd{display:block;line-height:48px;color:inherit}
label,input,button{font-size:1rem;margin:4px 0}
input[type=text],input[type=range]{width:100%}
input[type=range]{min-height:44px}input[type=checkbox]{width:24px;height:24px}
#number,#msg{width:calc(100% - 24px);box-sizing:border-box}
button{min-height:44px;margin-right:8px}
#controls{margin:12px}
#demo{border:1px solid #333;border-radius:12px;padding:12px;margin:12px auto;max-width:420px}
@media (min-width:1000px){#demo{position:fixed;top:12px;left:calc(50% + 232px);width:260px;margin:0;z-index:80}}

#number,#msg,#controls button,#helpertoggle,#battery,#temp,#simbtn,#visbtn{min-height:48px}
#controls,#demo-controls{display:flex;flex-wrap:wrap;gap:8px}
#controls button,#demo-controls button{margin:0}
#demo .box>label{display:block;margin:8px 0}
#demo .box>label:last-child{min-height:48px;padding:12px}
#charge{appearance:none;-webkit-appearance:none;flex:none;width:48px;height:48px;margin:0;display:grid;place-content:center;background:transparent;border:0;padding:12px}
#charge::before{content:"";display:block;width:24px;height:24px;box-sizing:border-box;border:2px solid;border-radius:3px}
#charge:checked::before{content:"✓";text-align:center;font-size:20px;line-height:20px}
@@OPOS:phone-a.css@@
@@OPOS:phone-b.css@@
@@OPOS:jaryo.css@@
@@OPOS:jaryo-layout.css@@
</style></head><body>
@@OPOS:phone-a.html@@
<div id="phone">
@@OPOS:phone-b.html@@
@@ABOUT_NOTICES@@
@@OPOS:body-1.html@@
@@OPOS:body-2.html@@
@@OPOS:body-3.html@@
@@OPOS:body-4.html@@
</div><div id="demo" hidden><p id="status">ready</p><div class="box"><label>Battery <input id="battery" type="range" min="0" max="100" step="0.5" value="60"><output id="battery-val" for="battery">60%</output></label><br><label>Temp (°C) <input id="temp" type="range" min="15" max="60" step="0.5" value="30"><output id="temp-val" for="temp">30°C</output></label><label style="display:flex;align-items:center;gap:8px"><input id="charge" type="checkbox"> Charging</label></div><div id="demo-controls"><button id="simbtn">Start simulation</button><button id="visbtn">Show helper</button></div></div><script>
window.oposHooks=window.oposHooks||[];
@@OPOS:js-1.js@@
(function(){try{var a=G('about'),n=G('about-notices');if(a&&n)a.insertAdjacentElement('afterend',n)}catch(e){}})();
@@OPOS:js-2.js@@
@@OPOS:js-3.js@@
let helperCtl=null;let helperTimer=null;async function helperCall(o,ans){let expected=60;try{let v=parseInt(localStorage.getItem('opos_helper_s_'+o.action),10);if(isFinite(v)){expected=Math.max(5,Math.min(300,v))}}catch(e){}if(document.getElementById('helperwait'))return null;if(helperTimer){clearInterval(helperTimer);helperTimer=null}let oldP=G('helperwait');if(oldP&&oldP.parentNode)oldP.parentNode.removeChild(oldP);let oldB=G('helpercancel');if(oldB&&oldB.parentNode)oldB.parentNode.removeChild(oldB);let prog=E('progress');prog.id='helperwait';prog.setAttribute('aria-label','Waiting for the helper');prog.max=expected;prog.value=0;ans.parentNode.insertBefore(prog,ans.nextSibling);let btn=E('button');btn.id='helpercancel';btn.textContent='Cancel';ans.parentNode.insertBefore(btn,prog.nextSibling);let start=Date.now();let elapsed=0;let act='Thinking';if(o.web){act='Searching Wikipedia and thinking'}else if(o.action==='plan'){act='Planning'}else if(o.action==='explain'){act='Explaining'}function tick(){elapsed=Math.floor((Date.now()-start)/1000);if(elapsed<expected){ans.textContent=(act==='Thinking'?'Thinking... about ':act+'... about ')+(expected-elapsed)+' s left';prog.value=elapsed}else{ans.textContent=(act==='Thinking'?'Still thinking... ':'Still '+act.toLowerCase()+'... ')+elapsed+' s so far';prog.removeAttribute('value')}}tick();helperTimer=setInterval(tick,1000);helperCtl=new AbortController();const fin=()=>{clearInterval(helperTimer);helperTimer=null;if(prog.parentNode)prog.parentNode.removeChild(prog);if(btn.parentNode)btn.parentNode.removeChild(btn)};btn.onclick=function(){helperCtl.abort();fin();ans.textContent='Cancelled. Nothing changed on the phone.'};try{let r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':document.querySelector('meta[name=opos-token]').content},body:JSON.stringify(o),signal:helperCtl.signal});let j=await r.json();clearInterval(helperTimer);helperTimer=null;if(prog.parentNode)prog.parentNode.removeChild(prog);if(btn.parentNode)btn.parentNode.removeChild(btn);if(r.status===429){j={error:'Too many questions in a row. Wait a few seconds, then try again.'}}else if(r.status===403){j={error:'This page is out of date. Reload it, then try again.'}}else if(r.status>=500){j={error:'Something went wrong. Try again in a minute.'}}if(!j.error&&j.engine!=='rules'&&j.engine!=='none'&&j.engine!=='fixed order'){try{let s=Math.max(1,Math.round((Date.now()-start)/1000));localStorage.setItem('opos_helper_s_'+o.action,String(s))}catch(e){}}return j}catch(e){fin();if(e.name==='AbortError')return null;return{error:'The helper did not reply. Wait a few seconds, then try again.'}}}
function helperReset(){if(helperCtl){helperCtl.abort();helperCtl=null}if(helperTimer){clearInterval(helperTimer);helperTimer=null}let e=G('helperwait');if(e&&e.parentNode)e.parentNode.removeChild(e);let c=G('helpercancel');if(c&&c.parentNode)c.parentNode.removeChild(c);let ans=G('answer');if(ans)ans.textContent='Ready.';let rb=G('helperrestart');if(rb)rb.hidden=false}G('helperreset').onclick=helperReset;G('helperrestart').onclick=function(){helperReset();let ans=G('answer');if(ans)ans.textContent='Restarting the helper... this page reloads in a few seconds.';const back=()=>setInterval(healReload,2000);api({action:'restart_helper'}).then(r=>{if(r&&r.error){if(ans)ans.textContent=r.error;return}back()}).catch(back)};
@@OPOS:js-4.js@@
@@OPOS:js-5.js@@
 sync(G('thread'),'<b>Thread</b><br>'+(s.thread||[]).map(x=>esc(typeof x==='string'?x:(x.text||JSON.stringify(x)))).join('<br>'));
@@OPOS:js-6.js@@
   let bv=document.getElementById('battery-val');if(!bv){bv=document.createElement('span');bv.id='battery-val';G('battery').insertAdjacentElement('afterend',bv)}
   if(document.activeElement!==document.getElementById('battery')){document.getElementById('battery').value=s.battery.percent;document.getElementById('battery-val').textContent=Math.round(s.battery.percent)+'%'}
   if(s.temp_c!=null&&document.activeElement!==G('temp')){G('temp').value=s.temp_c;G('temp-val').textContent=s.temp_c+'°C'}G('charge').checked=!!s.charging;
@@OPOS:js-7.js@@
const renderBase=render;render=async function(s){const r=await renderBase(s);if(s&&!s.error){try{(window.oposHooks||[]).forEach(function(f){try{f(s)}catch(e){}})}catch(e){}}return r};
@@OPOS:js-8.js@@
</script>
<script>
@@OPOS:phone-a.js@@
</script>
<script>
@@OPOS:phone-b.js@@
</script>
<script>
@@OPOS:jaryo.js@@
</script>
<script>
@@OPOS:jaryo-layout.js@@
</script></body></html>"""
    html = apply_span(_inline_assets(html), span)
    html = html.replace('@@ABOUT_NOTICES@@', _about_notices(), 1)
    if demo:
        html = html.replace('<div id="demo" hidden>', '<div id="demo" data-demo="1">', 1)
    html = html.replace("<title>Old Phone OS Interface</title>", '<meta name="opos-token" content="' + html_escape(token, quote=True) + '"><title>Old Phone OS Interface</title>', 1)
    html = net_page.add_settings_link(html, os.environ)  # track: network (OPOS_NETWORK=1 only)
    return secure.add_nonce(html, nonce) if nonce else html
_RATE_ACTIONS = {"call": "call", "send": "send", "receive": "receive", "explain": "explain", "ask": "ask", "plan": "plan", "restart_helper": "restart_helper",
                 "update_check": "update_check", "update_download": "update_download", "update_rollback": "update_rollback"}
_INJECT_ACTIONS = ("receive",)
_UPDATE_ACTIONS = {"update_check": "check", "update_download": "start_download", "update_rollback": "request_rollback"}  # opos.update_client
# --- track: network (Wi-Fi and radio status): GET /network and the net_* actions; logic in opos.net, page in opos.net_page ---
def _net_serve(h):
    nonce = secrets.token_urlsafe(16)
    body = net_page.render(h.token, nonce).encode()
    if osk is not None:
        body = osk.inject_bytes(body, nonce)  # track osk: the password field needs the keyboard; the same bytes while OPOS_OSK is off
    h.send_response(200)
    for k, v in secure.security_headers(nonce).items():
        h.send_header(k, v)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(body)
def _net_act(h, a, data):
    if not h.limiter.allow(a):
        h._deny(429, "too many requests", "rate")
        return
    code, obj = net_page.handle(a, data)
    h._json(obj, code)
# --- end track: network ---
# --- track: monitor (the Inference page): GET /monitor and GET /api/monitor, only while OPOS_MONITOR=1; data in opos.monitor, glue in opos.monitor_web ---
def _monitor_serve(h, path):
    """Answer /monitor (the document) or /api/monitor (its data). The Host check has been made by _get."""
    if path == "/monitor":
        nonce = secrets.token_urlsafe(16)
        try:
            # The same inliner page() uses, then the two-panel rewrite, the token meta and the nonce (monitor_web.document).
            body = monitor_web.document(_inline_assets(_asset("monitor.html")), h.token, nonce, h.span).encode()
        except (OSError, ValueError) as e:  # a missing part, or a drop-in whose title is not the one expected: send nothing half-built
            logging.getLogger("opos.security").warning("monitor page not built: %s", type(e).__name__)
            h._json({"error": "internal error"}, 500)
            return
        h.send_response(200)
        for k, v in secure.security_headers(nonce).items():
            h.send_header(k, v)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)
        return
    if not secure.token_ok(h.headers.get("X-OPOS-Token"), h.token):
        h._deny(403, "forbidden", "token")
        return
    if h.command == "HEAD":  # a HEAD would start the monitor's collection and return nothing
        logging.getLogger("opos.security").warning("rejected: %s", "method")
        h.send_response(405)
        h.send_header("Allow", "GET")
        h.send_header("Content-Length", "0")
        for k, v in secure.security_headers().items():
            h.send_header(k, v)
        h.end_headers()
        return
    if not h.limiter.allow(monitor_web.LIMIT_KEY):  # an unknown key is refused, so a server built without the bucket answers 429
        h._deny(429, "too many requests", "rate")
        return
    with h.lock:
        snap = h.phone.state()
        health = getattr(getattr(h.phone, "sim", None), "health", None)
    # Outside the lock: a slow LAN host can use the monitor's whole 6 s budget, and the phone must go on answering meanwhile.
    h._json(monitor.get().snapshot(monitor_web.local_state(snap, health)))
# --- end track: monitor ---
class Handler(BaseHTTPRequestHandler):
    """Loopback web handler."""
    phone = None
    span = None
    lock = threading.Lock()
    DEADLINE = 15.0
    def setup(self):
        """Start deadline timer."""
        super().setup()
        self._deadline_lock = threading.Lock()
        self._expired = False
        self._deadline_at = time.monotonic() + type(self).DEADLINE
        self._timer = threading.Timer(type(self).DEADLINE, self._expire)
        self._timer.daemon = True
        self._timer.start()
    def _admit_request(self):
        """Decide whether execution may begin."""
        with self._deadline_lock:
            if self._expired or time.monotonic() >= self._deadline_at:
                self.close_connection = True
                return False
            return True
    def _expire(self):
        """Close slow connection."""
        with self._deadline_lock:
            self._expired = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass
    def parse_request(self):
        """Reject headers truncated by deadline."""
        result = super().parse_request()
        if getattr(self, "_expired", False):
            self.close_connection = True
            return False
        return result
    def _disarm(self):
        """Stop deadline timer."""
        try:
            self._timer.cancel()
        except AttributeError:
            pass
    def finish(self):
        """Cancel deadline timer."""
        self._disarm()
        super().finish()
    def log_message(self, *args):
        return
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in secure.security_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
    def _get(self):
        """Serve page or state."""
        self._disarm()
        if not secure.host_ok(self.headers.get("Host"), self.port):
            self._deny(403, "forbidden", "host")
            return
        path = urllib.parse.urlsplit(self.path).path
        if osk is not None and osk.serve(self, path):  # track osk: /osk.js and /osk.css are served only while OPOS_OSK=1 (src/opos/osk.py)
            return
        if path == "/network":  # track: network
            _net_serve(self)
            return
        if apps_page.serve_get(self, path):
            return
        if path in ("/monitor", "/api/monitor") and monitor_web is not None and monitor_web.enabled():  # track: monitor (OPOS_MONITOR=1 only)
            _monitor_serve(self, path)
            return
        if path in ("/", "/favicon.ico") or (path == "/update" and self.updater is not None):  # track ota: /update only where an updater exists
            nonce = secrets.token_urlsafe(16)
            if path == "/favicon.ico":
                body = FAVICON
            elif path == "/update":  # its own document: no text field (no keyboard) and no Settings (no links to add); the decorations below are for "/" only
                body = update_page.page(self.token, nonce, self.updater.snapshot()).encode()
            else:
                body = page(self.token, nonce, span=self.span).encode()
            self.send_response(200)
            for k, v in secure.security_headers(nonce).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "image/png" if path == "/favicon.ico" else "text/html; charset=utf-8")
            if path == "/" and osk is not None:
                body = osk.inject_bytes(body, nonce)  # track osk: adds the keyboard's two tags while OPOS_OSK=1, else the same bytes
            if path == "/" and monitor_web is not None:
                body = monitor_web.link_bytes(body)  # track monitor: the Settings link to /monitor while OPOS_MONITOR=1, else the same bytes
            if path == "/":
                body = apps_page.inject_bytes(body, nonce)  # More apps, only when OPOS_APPS names an app; otherwise the same bytes
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        elif path == "/api/state":
            if not secure.token_ok(self.headers.get("X-OPOS-Token"), self.token):
                self._deny(403, "forbidden", "token")
                return
            with self.lock:
                snap = self.phone.state()
            if lockpin.is_locked():
                snap = lockpin.redact(snap)
            self._json(snap)
        elif path == "/api/lock" and lockpin.active():
            if not secure.token_ok(self.headers.get("X-OPOS-Token"), self.token):
                self._deny(403, "forbidden", "token")
                return
            body = lockpin.status()
            body["lock"] = lockpin.counts()
            self._json(body)
        elif path == "/api/update" and self.updater is not None:
            if not secure.token_ok(self.headers.get("X-OPOS-Token"), self.token):
                self._deny(403, "forbidden", "token")
                return
            self._json(self.updater.snapshot())
        else:
            self._json({"error": "not found"}, 404)
    def do_GET(self):
        """Handle GET."""
        if not self._admit_request():
            return
        try:
            self._get()
        except Exception:
            logging.getLogger("opos.security").warning("internal error")
            try:
                self._json({"error": "internal error"}, 500)
            except OSError:
                pass
    do_HEAD = do_GET
    token = ""
    port = 0
    limiter = None
    allow_inject = False
    updater = None
    timeout = 10
    def _deny(self, code, message, reason):
        """Reject with JSON error."""
        logging.getLogger("opos.security").warning("rejected: %s", reason)
        self._json({"error": message}, code)
        if self.command == "POST":
            try:
                n = int(self.headers.get("Content-Length"))
                if n <= 0:
                    return
            except (ValueError, TypeError):
                return
            try:
                self.connection.settimeout(0.2)
                self.rfile.read(min(n, secure.MAX_BODY))
            except OSError:
                pass
    def _no(self):
        """Reject unsupported method."""
        if not self._admit_request():
            return
        logging.getLogger("opos.security").warning("rejected: %s", "method")
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST")
        self.send_header("Content-Length", "0")
        for k, v in secure.security_headers().items():
            self.send_header(k, v)
        self.end_headers()
    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no
    def send_error(self, code, message=None, explain=None):
        """Send JSON error."""
        try:
            err = http.HTTPStatus(code).phrase.lower()
        except ValueError:
            err = "error"
        self._json({"error": err}, code)
    def handle(self):
        """Dispatch one connection."""
        try:
            super().handle()
        except ConnectionError:
            logging.getLogger("opos.security").debug("client disconnected")
            return
        except Exception:
            logging.getLogger("opos.security").warning("internal error")
    def do_POST(self):
        """Handle POST."""
        if not self._admit_request():
            return
        try:
            self._post()
        except Exception:
            logging.getLogger("opos.security").warning("internal error")
            try:
                self._json({"error": "internal error"}, 500)
            except OSError:
                pass
    def _post(self):
        """Handle API POST."""
        path = urllib.parse.urlsplit(self.path).path
        app_post = apps_page.is_post(path)
        lock_post = path == "/api/lock" and lockpin.active()
        if path != "/api" and not app_post and not lock_post:
            self._deny(404, "not found", "path")
            return
        if not secure.host_ok(self.headers.get("Host"), self.port):
            self._deny(403, "forbidden", "host")
            return
        if not secure.origin_ok(self.headers.get("Origin"), self.port):
            self._deny(403, "forbidden", "origin")
            return
        if not secure.json_type_ok(self.headers.get("Content-Type")):
            self._deny(415, "unsupported media type", "type")
            return
        if not secure.token_ok(self.headers.get("X-OPOS-Token"), self.token):
            self._deny(403, "forbidden", "token")
            return
        if not app_post and not self.limiter.allow("post"):
            self._deny(429, "too many requests", "rate")
            return
        try:
            limit = 70000 if path == "/api/notes" else secure.MAX_BODY
            data = secure.read_json_body(self.rfile, self.headers, limit)
        except secure.BodyError as e:
            self._deny(e.status, e.message, "body")
            return
        self._disarm()
        if lock_post:
            self._lock_act(data)
            return
        if lockpin.blocks(data.get("action") if isinstance(data.get("action"), str) else "", path):
            self._deny(403, "locked", "lock")
            return
        if app_post:
            apps_page.post(self, path, data)
            return
        a = data.get("action")
        if not isinstance(a, str):
            self._deny(400, "bad request", "action")
            return
        txt = lambda k: k in data and isinstance(data[k], str)
        if a in _INJECT_ACTIONS and not self.allow_inject:
            self._deny(403, "forbidden", "inject")
            return
        if a in _RATE_ACTIONS and not self.limiter.allow(a):
            self._deny(429, "too many requests", "rate")
            return
        if a in _UPDATE_ACTIONS:
            if self.updater is None or len(data) != 1:
                self._deny(400, "bad request", "shape")
                return
            try:
                getattr(self.updater, _UPDATE_ACTIONS[a])()
            except update_client.UpdateError as e:
                self._json({"error": e.message, "code": e.code}, e.status)
                return
            self._json(dict(self.updater.snapshot(), ok=True))
            return
        for k, m in (("number", 64), ("name", 64), ("time", 64), ("text", 1000)):
            v = data.get(k)
            if isinstance(v, str) and len(v) > m:
                self._deny(400, "bad request", "size")
                return
        if a in net_page.ACTIONS:  # track: network
            _net_act(self, a, data)
            return
        if a == "explain":
            with self.lock:
                req = self.phone.explain_routes()
            if req is None:
                self._json({"error": "helper hidden"})
            else:
                self._json(muse.explain(*req))
            return
        if a == "ask":
            with self.lock:
                vis = self.phone.visible
                facts = self.phone.ask_facts()
            if not vis:
                self._json({"error": "helper hidden"})
                return
            if txt("text"):
                ans = assist.answer(data["text"], web=data.get("web") is True, free=assist.free_mb(), context=facts)
                self._json(ans)
                return
        if a == "plan":
            with self.lock:
                req = self.phone.plan_routes()
            if req is None:
                self._json({"error": "helper hidden"})
                return
            order = muse.plan(req[0], req[1], req[2])
            route = getattr(order, "route", None)
            if route is None:
                self._json({"order": list(order), "engine": "fixed order", "where": "battery rules", "credit": "Battery rules kept this order"})
            else:
                change = "kept" if order == list(req[1]) else "changed"
                self._json({"order": list(order), "engine": route.name, "where": route.where, "credit": f"{route.name} {change} this order"})
            return
        if a == "restart_helper":
            if not os.environ.get("INVOCATION_ID"):
                self._json({"error": "Restart works only when OPOS runs as a service."})
                return
            _restart_service()
            self._json({"ok": True, "text": "Restarting the helper..."})
            return
        with self.lock:
            bad = not apply_phone_action(self.phone, data)
            if not bad:
                snap = self.phone.state()
        if bad:
            self._json({"error": "unknown action" if a not in {"battery","temp","call","hang_up","send","receive","open","visible","sim","tick","charge","goal","undo","in_use","explain","ask","plan","restart_helper"} else "bad request"}, 400)
            return
        self._json(snap)
    def _lock_act(self, data):
        """Set, skip, lock or unlock. Wrong-PIN waits are computed from data['now'] when a test sends it."""
        action = data.get("action") if isinstance(data, dict) else None
        if lockpin.blocks(action if isinstance(action, str) else "", "/api/lock"):
            self._deny(403, "locked", "lock")
            return
        try:
            if action == "set":
                lockpin.set_pin(data.get("pin"))
                self._json(lockpin.status())
                return
            if action == "skip":
                lockpin.skip()
                self._json(lockpin.status())
                return
            if action == "lock":
                lockpin.lock()
                self._json(lockpin.status())
                return
            if action == "unlock":
                now = data.get("now")
                if isinstance(now, bool) or not isinstance(now, (int, float)):
                    now = time.time()
                ok, status = lockpin.unlock("" if data.get("pin") is None else str(data.get("pin")), now)
                self._json(status, 200 if ok else 403)
                return
        except ValueError:
            self._json({"error": "bad request"}, 400)
            return
        self._json({"error": "bad request"}, 400)
def apply_phone_action(phone, data):
    """Apply only a validated in-memory phone action; return whether its shape was valid.

    Helper inference and host operations deliberately live outside this dispatcher.
    Callers own authorization, body/string limits and synchronization.
    """
    a = data.get("action")
    bad = False
    def num(k):
        if k not in data:
            return False
        v = data[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        try:
            f = float(v)
        except (OverflowError, ValueError, TypeError):
            return False
        return math.isfinite(f)
    txt = lambda k: k in data and isinstance(data[k], str)
    if a == "battery" and num("level" if "level" in data else "value"):
        phone.set_battery(data.get("level", data.get("value")))
    elif a == "temp" and num("temp" if "temp" in data else "value"):
        phone.set_temp(data.get("temp", data.get("value")))
    elif a == "call" and txt("number"):
        phone.call(data["number"])
    elif a == "hang_up":
        phone.hang_up()
    elif a == "send" and txt("number") and txt("text"):
        phone.send(data["number"], data["text"])
    elif a == "receive" and txt("number") and txt("text"):
        phone.receive(data["number"], data["text"])
    elif a == "open" and txt("number"):
        phone.open(data["number"])
    elif a == "visible" and isinstance(data.get("visible"), bool):
        phone.set_visible(data["visible"])
    elif a == "sim" and isinstance(data.get("running"), bool) and ("speed" not in data or num("speed")):
        phone.set_sim(data["running"], data.get("speed"))
    elif a == "tick" and ("real_s" not in data or num("real_s")):
        try:
            phone.tick(data.get("real_s", 1))
        except ValueError:
            bad = True
    elif a == "charge" and isinstance(data.get("on"), bool):
        phone.charging = data["on"]
    elif a == "goal" and isinstance(data.get("time"), str):
        try:
            phone.dash.set_goal(data["time"], phone._goal_clock())
        except ValueError:
            bad = True
    elif a == "undo" and isinstance(data.get("name"), str):
        try:
            phone.undo(data["name"])
        except ValueError:
            bad = True
    elif a == "in_use":
        name = data.get("name", "")
        if isinstance(name, str):
            try:
                phone.dash.set_in_use(name)
            except ValueError:
                bad = True
        else:
            bad = True
    else:
        bad = True
    return not bad


class _Limits:
    """Per-action rate limits."""
    def __init__(self):
        self._buckets = {"post": secure.RateLimiter(20.0, 40), "send": secure.RateLimiter(0.2, 5), "call": secure.RateLimiter(0.2, 5), "receive": secure.RateLimiter(2.0, 20), "explain": secure.RateLimiter(0.2, 3), "ask": secure.RateLimiter(0.2, 3), "plan": secure.RateLimiter(0.2, 3), "restart_helper": secure.RateLimiter(0.2, 3),
                         "update_check": secure.RateLimiter(0.2, 3), "update_download": secure.RateLimiter(0.1, 2), "update_rollback": secure.RateLimiter(0.1, 2)}
        self._buckets.update(net_page.buckets())  # track: network
        self._buckets.update(apps_page.buckets())
        if monitor_web is not None:
            self._buckets.update(monitor_web.buckets())  # track: monitor
    def allow(self, key):
        """Delegate key to its bucket."""
        b = self._buckets.get(key)
        return b.allow("all") if b is not None else False
def make_server(port, phone, host="127.0.0.1", token=None, span=None, updater=None):
    """Bind loopback server. updater: an opos.update_client.UpdateClient (default: this install's)."""
    secure.check_bind(host)
    class Bound(Handler):
        pass
    Bound.phone = phone
    Bound.span = span
    if token is not None:
        _validate_token(token)
    Bound.token = token if token else secure.new_token()
    Bound.limiter = _Limits()
    Bound.allow_inject = type(phone) is Phone and not phone.live  # a real phone takes no injected texts
    Bound.updater = updater if updater is not None else update_client.UpdateClient()
    server = _Server((host, port), Bound)
    Bound.port = server.server_address[1]
    return server
def _install_helper_visible(flag):
    """Install step j7 (docs/jaryo.md section 6): Helper On starts the service visible. --visible always wins."""
    if flag:
        return True
    try:
        saved = prefs.load()
        return bool(saved["applied"]) and prefs.effective(saved).get("helper") == "on"
    except Exception:
        return False
DEMO_GOAL = "16:00"  # the plain quickstart demo's goal, so the 60% / 60% health / noon demo starts in Saver with email still allowed
def _arm_configured_goal(phone, cfg):
    """Set the saved goal, read against the clock goals may use (none while a live clock is a guess)."""
    goal = cfg["goal"]
    phone.dash.set_goal(goal["time"] if goal["enabled"] else None, phone._goal_clock())
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--visible", action="store_true")
    p.add_argument("--token-file", default=None)
    p.add_argument("--live-battery", action="store_true", help="read this phone's battery and clock instead of simulating (also OPOS_BATTERY=live)")
    p.add_argument("--span", metavar="PANE,HINGE", help="the window covers both panels of a two-screen phone: pane and hinge widths in CSS px, 675,42 on the Surface Duo 1 (also OPOS_SPAN; off disables)")
    args = p.parse_args(argv)
    import sys
    span = None
    try:
        span = parse_span(args.span if args.span is not None else os.environ.get("OPOS_SPAN"))
    except ValueError as e:
        if args.span is not None:
            p.error(f"--span: {e}")
        print(f"opos: ignoring OPOS_SPAN: {e}", file=sys.stderr)
    # >>> track firstboot: Duo OS first run and time zone (src/opos/firstrun.py); does nothing unless OPOS_FIRSTRUN=1.
    # It applies the saved time zone, so it must run before Phone() reads the clock.
    try:
        from opos import firstrun as _firstrun
        _firstrun.install(sys.modules[__name__])
    except Exception as e:  # never let first run keep the phone from starting
        print("opos: first run not available:", type(e).__name__, file=sys.stderr)
    try:
        from opos import apps as _apps
        if "clock" in (_apps.names_from() or []):
            from opos import clock_page as _clock_page
            _clock_page.start_thread()
    except Exception as e:
        print("opos: clock not available:", type(e).__name__, file=sys.stderr)
    # <<< track firstboot
    live = None
    if args.live_battery or os.environ.get("OPOS_BATTERY") == "live":
        from opos.hw.live_battery import LiveBattery
        live = LiveBattery()
        if not live.ok:
            print("opos: no readable battery (termux-battery-status or sysfs); using the simulation", file=sys.stderr)
            live = None
    token = None
    if args.token_file is not None and tokenfile.usable(args.token_file, sys.stderr):  # first start: make it, mode 0600, so a restart keeps the token
        try:
            with open(args.token_file, encoding="utf-8") as f:
                token = f.read().strip()
            _validate_token(token)
        except Exception:
            print("opos: cannot read session token", file=sys.stderr)
            raise SystemExit(2)
    phone = Phone(visible=_install_helper_visible(args.visible), live_battery=live)
    if live is not None and not phone.clock_ok:
        print("opos: the clock is not set (before 2026, or only the image's floor); goal times stay off until it is", file=sys.stderr)
    try:
        cfg = setup_config.load()
        if cfg is None:
            cfg = setup_config.default_config()
            cfg["goal"] = {"enabled": live is None, "time": DEMO_GOAL}  # a real phone has no goal until its owner picks one
    except Exception:
        cfg = setup_config.default_config()
    _arm_configured_goal(phone, cfg)
    _route_with_glimmer(phone, os.environ)
    server = make_server(args.port, phone, token=token, **({} if span is None else {"span": span}))
    if span is not None:
        print(f"OPOS two-screen span: {span.pane:g} + {span.hinge:g} + {span.pane:g} CSS px", flush=True)
    server.RequestHandlerClass.updater.start_auto_check()  # only when /etc/opos/update.json opts in with auto_check; it checks, never downloads
    try:
        port = server.server_address[1]  # not args.port: --port 0 lets the kernel pick, and the update smoke test reads the real one from this line
    except (AttributeError, IndexError, TypeError):
        port = args.port
    if type(port) is not int:  # a test double
        port = args.port
    if live is not None:
        print(f"OPOS live battery ({live.source}, health {live.health_source}): http://127.0.0.1:{port}/ (Ctrl-C to stop)", flush=True)
    else:
        print(f"OPOS desktop demo: http://127.0.0.1:{port}/?demo=1 (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass



if __name__ == "__main__":
    main()
