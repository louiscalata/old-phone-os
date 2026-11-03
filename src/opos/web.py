# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import dataclasses
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
import opos.home
import opos.reserve
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
from opos.runtime import DEFAULT_TASKS, Runtime
from opos.sim import DrainSim
from opos.ui import ScreenModem
from opos.dashboard import Dashboard
from opos.battery import essentials_reserve
from opos.governor import PowerState, rule_decide
from opos.power import plan
from opos.router import afford
from opos import notices, secure


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


_TASK_COST = {t.name: t for t in DEFAULT_TASKS}
_REDACT_RE = re.compile(r'\b(AI|model|Governor|assistant)\b', re.IGNORECASE)
def _redact_text(s):
    if not isinstance(s, str):
        return s
    return _REDACT_RE.sub('...', s)
_NOTICE_FORBIDDEN_RE = re.compile(r'\b(AI|model|Governor|assistant|battery)\b', re.IGNORECASE)
_EMPTY_NOTICES = {
    "error: battery empty, charge the phone to call": "Charge the phone to call",
    "error: battery empty, charge the phone to send": "Charge the phone to send",
    "call ended: battery empty": "Call ended. Charge the phone",
}
def notice_for(status, in_call):
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
    if in_call:
        return "In call"
    if status == "call ended":
        return "Call ended"
    if status == "sent":
        return "Message sent"
    if status == "message received":
        return "New message"
    return ""
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
def lock_story(level, health, temp_c, charging, paused, goal=None, goal_met=True):
    """A short lock-screen fact, using effective pauses rather than helper visibility."""
    prefix = ("Charging. " if charging else "Phone hot. " if temp_c >= 45 else
              "Battery low. " if level < 20 else "Worn battery. " if health < 0.8 else "")
    names = sorted(set(paused))
    suffix = ""
    if goal and not charging:
        if not goal_met:
            suffix = f"; {goal} unlikely"
        elif names:
            suffix = f" for {goal}"
    action = _join(names).capitalize() + " paused" if names else "Nothing paused"
    if len(prefix + action + suffix) > 48 and names:
        action = f"{len(names)} task{'s' if len(names) != 1 else ''} paused"
    return prefix + action + suffix + "."
def why_paused(level, health, temp_c, charging, paused, goal=None, goal_met=True, time_left=None, *, usable_percent=None):
    """One plain sentence, from the numbers alone, that the helper repeats for why-questions."""
    pct, hp = int(round(level)), int(round(health * 100))
    usable = int(level * health) if usable_percent is None else usable_percent
    if health < 0.8:
        s = f"The battery is worn ({hp}% health), so {pct}% charge is about {usable}% usable"
    else:
        s = f"The battery is at {pct}% ({hp}% health, about {usable}% usable)"
    if temp_c >= 45:
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
class Phone:
    def __init__(self, visible=False):
        self.visible = bool(visible)
        self.runtime = Runtime(visible=self.visible)
        self.modem = ScreenModem()
        self.essentials = Essentials(self.modem)
        self.book = Book(DEMO_CONTACTS)
        self.sim = DrainSim(60.0, health=0.6, temp_c=30.0, speed=1.0)  # Saver at start: the helper answers, maps and photos backup rest
        self.dash = Dashboard(self.runtime.tasks)
        self.clock_s = 43200
        self.charging = False
        self.running = False
        self.open_number = None
        self.status = "ready"
        self._in_call = False
        self._tick_obj = None
        self._last_tick_time = time.monotonic()
    def _tick(self):
        try:
            self._tick_obj = self.runtime.step(self.sim.t_s, self.sim.level, health=self.sim.health, temp_c=self.sim.temp_c, charging=self.charging, goal_hours=self.dash.hours_to_goal(self.clock_s))
        except TypeError:
            self._tick_obj = self.runtime.step(self.sim.t_s, self.sim.level, health=self.sim.health, temp_c=self.sim.temp_c)
        tick = self._tick_obj
        d = tick.decision
        name = self.dash.in_use
        names = {t.name for t in self.runtime.tasks}
        if name in names and name in d.deferred and self.sim.level > tick.reserve:
            new_deferred = tuple(x for x in d.deferred if x != name)
            allowed = d.allowed
            if name in allowed:
                new_allowed = allowed
            else:
                new_allowed = tuple(allowed) + (name,)
            new = dataclasses.replace(d, deferred=new_deferred, allowed=new_allowed)
            if not self.charging:
                new = opos.reserve.apply_reserve(new, self.sim.level, self.sim.temp_c, self.sim.health, self.runtime.tasks)
            d = new
        restored = tuple(t.name for t in self.runtime.tasks if t.name in self.dash.undone and t.name in d.deferred)
        if restored:
            d = dataclasses.replace(d, deferred=tuple(n for n in d.deferred if n not in restored),
                                    allowed=d.allowed + tuple(n for n in restored if n not in d.allowed))
        if d != tick.decision:
            if d.reason.startswith("router+glimmer"):
                state = PowerState(self.sim.level, self.charging, self.sim.health, self.sim.temp_c,
                                   self.dash.hours_to_goal(self.clock_s))
                rules = rule_decide(state, self.runtime.tasks)
                if not any(t.name in d.deferred and t.name in rules.allowed and afford(state, t)[0]
                           for t in self.runtime.tasks):
                    d = dataclasses.replace(d, reason=f"router: allowed {len(d.allowed)}, deferred {len(d.deferred)}")
            screen = opos.home.home_screen(self.sim.level, d, visible=self.visible)
            self._tick_obj = dataclasses.replace(tick, decision=d, screen=screen)
        return self._tick_obj
    def paused_names(self):
        return set(self.dash.paused_names)
    def _paused_reasons(self, tick):
        """Explain current pauses without asking the helper or changing its decision."""
        if not self.dash.paused_names:
            return {}
        hours = self.dash.hours_to_goal(self.clock_s)
        state = PowerState(self.sim.level, self.charging, self.sim.health, self.sim.temp_c, hours)
        rules = rule_decide(state, self.runtime.tasks)
        planned = plan(state.level, hours, self.runtime.tasks, state.health, state.charging, self.dash.in_use).paused
        waived = hours is not None and not state.charging and state.temp_c < 45
        reserve = state.level <= essentials_reserve(None) or (state.level <= tick.reserve and not waived) or tick.decision.reason == opos.reserve.WORN_REASON
        reasons = {}
        for task in self.runtime.tasks:
            if task.name not in self.dash.paused_names:
                continue
            allowed, cause = afford(state, task)
            who, why = "Battery rules", "to save power for calls and messages"
            if reserve:
                why = "to keep a battery reserve for calls"
            elif not allowed:
                why = {"too hot": "because the phone is hot", "would miss your goal": f"to help the battery last until {self.dash.goal}", "battery below 80 percent": "because usable battery is below 80%" if self.visible else why, "not enough for the next 4 hours": "to keep power for the next 4 hours"}.get(cause, why)
            elif task.name in rules.deferred:
                why = "because the phone is hot" if state.temp_c >= 45 else "because usable battery is low" if self.visible else why
            elif task.name in planned:
                why = f"to help the battery last until {self.dash.goal}"
            elif task.name in tick.decision.deferred and tick.decision.reason.startswith("router+glimmer"):
                who = "Muse Glimmer"
            reasons[task.name] = f"{who} paused this {why}."
        return reasons
    def _essential(self, name):
        t = _TASK_COST.get(name)
        if t is None:
            return True
        p = t.priority
        if isinstance(p, str):
            return p == "essential"
        return name in {"calls", "call", "sms", "messages", "phone"}
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
        out = {"screen": [html.escape(str(s)) for s in tick.screen], "brightness": int(tick.budget.screen_brightness), "status": self.status, "in_call": self._in_call, "notice": notice_for(self.status, self._in_call), "helper_visible": self.visible, "running": self.running, "contacts": [_contact(c) for c in DEMO_CONTACTS], "threads": threads(self.book, self.essentials.log), "thread": thread(self.book, self.essentials.log, self.open_number) if self.open_number else []}
        # Only the active, known in-memory modem is identified as simulated.
        out["simulation"] = {"modem": type(getattr(self.essentials, "modem", None)) is ScreenModem}
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
        out["prefs"] = public_prefs
        out["temp_c"] = round(float(self.sim.temp_c), 1)
        out["charging"] = bool(self.charging)
        if not self.visible:
            for t in out.get('threads', []):
                for k in ('preview', 'last', 'text'):
                    if isinstance(t.get(k), str):
                        t[k] = _redact_text(t[k])
            tl = out.get('thread', [])
            for i, item in enumerate(tl):
                if isinstance(item, str):
                    tl[i] = _redact_text(item)
                elif isinstance(item, dict) and isinstance(item.get('text'), str):
                    item['text'] = _redact_text(item['text'])
        free = assist.free_mb()
        if self.visible:
            out["helper"] = {"mode": d.mode.name, "allowed": list(d.allowed), "paused": list(d.deferred), "hours_left": None if math.isinf(tick.hours_left) else round(tick.hours_left, 1), "reserve": round(tick.reserve, 1), "source": "router" if d.reason.startswith("router") else "rules", "engine": muse.label(assist.ask_route(free=free))}
        out["battery"] = self.dash.view(self.sim.level, d.mode, self.charging, self.sim.health, self.clock_s, self.visible, d.reason, deferred=d.deferred)
        reasons = self._paused_reasons(tick)
        for item in out["battery"]["paused"]:
            item["reason"] = reasons[self.runtime.tasks[int(item["id"][1:])].name]
        out["lock_story"] = lock_story(self.sim.level, self.sim.health, self.sim.temp_c, self.charging, self.dash.paused_names, self.dash.goal, out["battery"]["goal_met"])
        if self.visible and "helper" in out:
            paused = self.paused_names()
            out["helper"]["allowed"] = [n for n in out["helper"]["allowed"] if n not in paused]
            for n in paused:
                if n not in out["helper"]["paused"]:
                    out["helper"]["paused"].append(n)
            hf = out["helper"]
            hf["battery_percent"] = int(round(self.sim.level))
            hf["battery_health_percent"] = int(round(self.sim.health * 100))
            hf["usable_percent"] = int(self.sim.level * self.sim.health)  # floor: 14.7% usable must not read as 15
            hf["temp_c"] = out["temp_c"]
            hf["charging"] = out["charging"]
            hf["goal"] = self.dash.goal
            hf["goal_met"] = bool(out["battery"]["goal_met"])
            hf["time_left"] = out["battery"]["time_left"]
            goal_label = self.dash.goal
            goal_hours = self.dash.hours_to_goal(self.clock_s)
            if goal_hours is not None and self.clock_s + goal_hours * 3600 >= 86400:
                goal_label += " tomorrow"
            hf["why_paused"] = why_paused(self.sim.level, self.sim.health, self.sim.temp_c, self.charging, hf["paused"], goal_label, goal_met=hf["goal_met"], time_left=hf["time_left"], usable_percent=hf["usable_percent"])
        h = int(self.clock_s // 3600) % 24
        m = int((self.clock_s % 3600) // 60)
        out["clock"] = f"{h:02d}:{m:02d}"
        out["lean"] = {"opos_mb": self_rss_mb(), "free_mb": free}
        out["poll_ms"] = poll_ms(d.mode.name, free, self._in_call)
        return out
    def _helper_request(self):
        """Facts, saving steps and the ordered routes (Spark Open first when the goal is hard)."""
        s = self.state()
        h = s["helper"]
        goal = self.dash.hours_to_goal(self.clock_s)
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
        self.sim.level = v
        self.status = "ready"
        self.dash.reset_history(self.sim.level)
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
        if round(self.sim.level) <= 0:
            self.status = "error: battery empty, charge the phone to call"
            return
        if self._in_call:
            self.status = "error: already in a call"
            return
        try:
            self.essentials.call(number)
            self._in_call = True
            self.status = "in call"
        except ValueError:
            self.status = "error: number not valid"
        except RuntimeError:
            self.status = "error: already in a call"
    def hang_up(self):
        try:
            self.essentials.end_call()
            self._in_call = False
            self.status = "call ended"
        except RuntimeError:
            self.status = "error: not in a call"
        except ValueError:
            self.status = "error: number not valid"
    def send(self, number, text):
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
        try:
            real_ok = isinstance(real_s, bool) or not isinstance(real_s, (int, float)) or not math.isfinite(float(real_s)) or float(real_s) < 0
        except (OverflowError, ValueError, TypeError):
            raise ValueError("bad real_s")
        if real_ok:
            raise ValueError("bad real_s")
        now = time.monotonic()
        elapsed = now - self._last_tick_time
        if elapsed < 0:
            elapsed = 0.0
        dt = elapsed if elapsed <= 5.0 else 5.0
        self._last_tick_time = now
        tick_obj = self._tick()
        paused = self.paused_names()
        undone = set(getattr(self.dash, "undone", set()) or set())
        costs = []
        counted = set()
        for name in tick_obj.decision.allowed:
            if name in paused:
                continue
            t = _TASK_COST.get(name)
            if t is None:
                continue
            if not self._essential(name):
                costs.append(t.cost)
                counted.add(name)
        for name in undone:
            if name in paused:
                continue
            if name in counted:
                continue
            t = _TASK_COST.get(name)
            if t is None:
                continue
            if not self._essential(name):
                costs.append(t.cost)
        speed = getattr(self.sim, 'speed', 1.0)
        self.clock_s = (self.clock_s + dt * speed) % 86400
        if self.charging:
            try:
                self.sim.charge(dt)
            except AttributeError:
                fn = getattr(self.sim, "step", None) or getattr(self.sim, "advance", None) or getattr(self.sim, "tick")
                try:
                    fn(dt, tick_obj.decision.mode.name, sum(costs))
                except TypeError:
                    fn(dt, tick_obj.decision.mode.name, costs)
        else:
            fn = getattr(self.sim, "step", None) or getattr(self.sim, "advance", None) or getattr(self.sim, "tick")
            try:
                fn(dt, tick_obj.decision.mode.name, sum(costs))
            except TypeError:
                fn(dt, tick_obj.decision.mode.name, costs)
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
            'Licence, privacy and tested limits</summary>'
            '<pre id="about-notices-text" style="white-space:pre-wrap;'
            'overflow-wrap:anywhere;font:inherit">'
            + html_escape(notices.as_text()) + '</pre></details></div>')

def page(token="", nonce="", *, demo=False):
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Old Phone OS</title><style>
@@OPOS:css-1.css@@
@@OPOS:css-2.css@@
@@OPOS:css-3.css@@
@@OPOS:css-4.css@@
.box{background:#222;border-radius:8px;padding:8px;margin:6px 12px;white-space:pre-wrap}
#answer a{color:#9ecbff;display:inline-block;min-height:44px;line-height:44px}
label,input,button{font-size:1rem;margin:4px 0}
input[type=text],input[type=range]{width:100%}
input[type=range]{min-height:44px}input[type=checkbox]{width:24px;height:24px}
#number,#msg{width:calc(100% - 24px);box-sizing:border-box}
button{min-height:44px;margin-right:8px}
#controls{margin:12px}
#demo{background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:12px;margin:12px auto;max-width:420px}
#demo label{color:#eee}
@media (min-width:1000px){#demo{position:fixed;top:12px;left:calc(50% + 232px);width:260px;margin:0;z-index:80}}

#number,#msg,#controls button,#helpertoggle,#battery,#temp,#simbtn,#visbtn{min-height:48px}
#controls,#demo-controls{display:flex;flex-wrap:wrap;gap:8px}
#controls button,#demo-controls button{margin:0}
#demo .box>label{display:block;margin:8px 0}
#demo .box>label:last-child{min-height:48px;padding:12px}
#charge{appearance:none;-webkit-appearance:none;flex:none;width:48px;height:48px;margin:0;display:grid;place-content:center;background:transparent;border:0;padding:12px}
#charge::before{content:"";display:block;width:24px;height:24px;box-sizing:border-box;border:2px solid #aaa;border-radius:3px;background:#222}
#charge:checked::before{content:"✓";background:#fff;color:#111;border-color:#fff;text-align:center;font-size:20px;line-height:20px}
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
</div><div id="demo" hidden><p id="status">ready</p><div class="box"><label>Battery <input id="battery" type="range" min="0" max="100" step="0.5" value="60"><output id="battery-val" for="battery">60%</output></label><br><label>Temp (°C) <input id="temp" type="range" min="15" max="60" step="0.5" value="30"></label><label style="display:flex;align-items:center;gap:8px"><input id="charge" type="checkbox"> Charging</label></div><div id="demo-controls"><button id="simbtn">Start simulation</button><button id="visbtn">Show helper</button></div></div><script>
window.oposHooks=window.oposHooks||[];
@@OPOS:js-1.js@@
(function(){try{var a=G('about'),n=G('about-notices');if(a&&n)a.insertAdjacentElement('afterend',n)}catch(e){}})();
@@OPOS:js-2.js@@
@@OPOS:js-3.js@@
let helperCtl=null;let helperTimer=null;async function helperCall(o,ans){let expected=60;try{let v=parseInt(localStorage.getItem('opos_helper_s_'+o.action),10);if(isFinite(v)){expected=Math.max(5,Math.min(300,v))}}catch(e){}if(document.getElementById('helperwait'))return null;if(helperTimer){clearInterval(helperTimer);helperTimer=null}let oldP=G('helperwait');if(oldP&&oldP.parentNode)oldP.parentNode.removeChild(oldP);let oldB=G('helpercancel');if(oldB&&oldB.parentNode)oldB.parentNode.removeChild(oldB);let prog=E('progress');prog.id='helperwait';prog.setAttribute('aria-label','Waiting for the helper');prog.max=expected;prog.value=0;ans.parentNode.insertBefore(prog,ans.nextSibling);let btn=E('button');btn.id='helpercancel';btn.textContent='Cancel';ans.parentNode.insertBefore(btn,prog.nextSibling);let start=Date.now();let elapsed=0;let act='Thinking';if(o.web){act='Searching Wikipedia and thinking'}else if(o.action==='plan'){act='Planning'}else if(o.action==='explain'){act='Explaining'}function tick(){elapsed=Math.floor((Date.now()-start)/1000);if(elapsed<expected){ans.textContent=(act==='Thinking'?'Thinking... about ':act+'... about ')+(expected-elapsed)+' s left';prog.value=elapsed}else{ans.textContent=(act==='Thinking'?'Still thinking... ':'Still '+act.toLowerCase()+'... ')+elapsed+' s so far';prog.removeAttribute('value')}}tick();helperTimer=setInterval(tick,1000);helperCtl=new AbortController();const fin=()=>{clearInterval(helperTimer);helperTimer=null;if(prog.parentNode)prog.parentNode.removeChild(prog);if(btn.parentNode)btn.parentNode.removeChild(btn)};btn.onclick=function(){helperCtl.abort();fin();ans.textContent='Cancelled. Nothing changed on the phone.'};try{let r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':document.querySelector('meta[name=opos-token]').content},body:JSON.stringify(o),signal:helperCtl.signal});let j=await r.json();clearInterval(helperTimer);helperTimer=null;if(prog.parentNode)prog.parentNode.removeChild(prog);if(btn.parentNode)btn.parentNode.removeChild(btn);if(r.status===429){j={error:'Too many questions in a row. Wait a few seconds, then try again.'}}else if(r.status===403){j={error:'This page is out of date. Reload it, then try again.'}}else if(r.status>=500){j={error:'Something went wrong in OPOS. Try again in a minute.'}}if(!j.error&&j.engine!=='rules'&&j.engine!=='none'&&j.engine!=='fixed order'){try{let s=Math.max(1,Math.round((Date.now()-start)/1000));localStorage.setItem('opos_helper_s_'+o.action,String(s))}catch(e){}}return j}catch(e){fin();if(e.name==='AbortError')return null;return{error:'OPOS is not responding. Wait a few seconds, reload this page, then try again.'}}}
function helperReset(){if(helperCtl){helperCtl.abort();helperCtl=null}if(helperTimer){clearInterval(helperTimer);helperTimer=null}let e=G('helperwait');if(e&&e.parentNode)e.parentNode.removeChild(e);let c=G('helpercancel');if(c&&c.parentNode)c.parentNode.removeChild(c);let ans=G('answer');if(ans)ans.textContent='Ready.';let rb=G('helperrestart');if(rb)rb.hidden=false}G('helperreset').onclick=helperReset;document.getElementById('helperrestart').onclick=function(){helperReset();let ans=G('answer');if(ans)ans.textContent='Restarting the helper... this page reloads in a few seconds.';api({action:'restart_helper'}).then(r=>{if(r&&r.error){if(ans)ans.textContent=r.error;return}setTimeout(()=>location.reload(),6000)}).catch(()=>{setTimeout(()=>location.reload(),6000)})};
@@OPOS:js-4.js@@
@@OPOS:js-5.js@@
 document.querySelectorAll('#threads button').forEach(b=>b.onclick=async()=>{G('number').value=b.dataset.n;render(await api({action:'open',number:b.dataset.n}))});
 G('thread').innerHTML='<b>Thread</b><br>'+(s.thread||[]).map(x=>esc(typeof x==='string'?x:(x.text||JSON.stringify(x)))).join('<br>');
@@OPOS:js-6.js@@
   let bv=document.getElementById('battery-val');if(!bv){bv=document.createElement('span');bv.id='battery-val';G('battery').insertAdjacentElement('afterend',bv)}
   if(document.activeElement!==document.getElementById('battery')){document.getElementById('battery').value=s.battery.percent;document.getElementById('battery-val').textContent=Math.round(s.battery.percent)+'%'}
   if(s.temp_c!=null&&document.activeElement!==G('temp'))G('temp').value=s.temp_c;G('charge').checked=!!s.charging;
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
    html = _inline_assets(html)
    html = html.replace('@@ABOUT_NOTICES@@', _about_notices(), 1)
    if demo:
        html = html.replace('<div id="demo" hidden>', '<div id="demo" data-demo="1">', 1)
    html = html.replace("<title>Old Phone OS</title>", '<meta name="opos-token" content="' + html_escape(token, quote=True) + '"><title>Old Phone OS</title>', 1)
    return secure.add_nonce(html, nonce) if nonce else html
_RATE_ACTIONS = {"call": "call", "send": "send", "receive": "receive", "explain": "explain", "ask": "ask", "plan": "plan", "restart_helper": "restart_helper"}
_INJECT_ACTIONS = ("receive",)
class Handler(BaseHTTPRequestHandler):
    """Loopback web handler."""
    phone = None
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
        self.wfile.write(body)
    def _get(self):
        """Serve page or state."""
        self._disarm()
        if not secure.host_ok(self.headers.get("Host"), self.port):
            self._deny(403, "forbidden", "host")
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            body = page(self.token, nonce).encode()
            self.send_response(200)
            for k, v in secure.security_headers(nonce).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            if not secure.token_ok(self.headers.get("X-OPOS-Token"), self.token):
                self._deny(403, "forbidden", "token")
                return
            with self.lock:
                snap = self.phone.state()
            self._json(snap)
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
    token = ""
    port = 0
    limiter = None
    allow_inject = False
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
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        for k, v in secure.security_headers().items():
            self.send_header(k, v)
        self.end_headers()
    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _no
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
        if self.path != "/api":
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
        if not self.limiter.allow("post"):
            self._deny(429, "too many requests", "rate")
            return
        try:
            data = secure.read_json_body(self.rfile, self.headers)
        except secure.BodyError as e:
            self._deny(e.status, e.message, "body")
            return
        self._disarm()
        a = data.get("action")
        if not isinstance(a, str):
            self._deny(400, "bad request", "action")
            return
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
        if a in _INJECT_ACTIONS and not self.allow_inject:
            self._deny(403, "forbidden", "inject")
            return
        if a in _RATE_ACTIONS and not self.limiter.allow(a):
            self._deny(429, "too many requests", "rate")
            return
        for k, m in (("number", 64), ("name", 64), ("time", 64), ("text", 1000)):
            v = data.get(k)
            if isinstance(v, str) and len(v) > m:
                self._deny(400, "bad request", "size")
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
        bad = False
        with self.lock:
            if a == "battery" and num("level" if "level" in data else "value"):
                self.phone.set_battery(data.get("level", data.get("value")))
            elif a == "temp" and num("temp" if "temp" in data else "value"):
                self.phone.set_temp(data.get("temp", data.get("value")))
            elif a == "call" and txt("number"):
                self.phone.call(data["number"])
            elif a == "hang_up":
                self.phone.hang_up()
            elif a == "send" and txt("number") and txt("text"):
                self.phone.send(data["number"], data["text"])
            elif a == "receive" and txt("number") and txt("text"):
                self.phone.receive(data["number"], data["text"])
            elif a == "open" and txt("number"):
                self.phone.open(data["number"])
            elif a == "visible" and isinstance(data.get("visible"), bool):
                self.phone.set_visible(data["visible"])
            elif a == "sim" and isinstance(data.get("running"), bool) and ("speed" not in data or num("speed")):
                self.phone.set_sim(data["running"], data.get("speed"))
            elif a == "tick" and ("real_s" not in data or num("real_s")):
                try:
                    self.phone.tick(data.get("real_s", 1))
                except ValueError:
                    bad = True
            elif a == "charge" and isinstance(data.get("on"), bool):
                self.phone.charging = data["on"]
            elif a == "goal" and isinstance(data.get("time"), str):
                try:
                    self.phone.dash.set_goal(data["time"])
                except ValueError:
                    bad = True
            elif a == "undo" and isinstance(data.get("name"), str):
                try:
                    self.phone.dash.undo(data["name"])
                except ValueError:
                    bad = True
            elif a == "in_use":
                name = data.get("name", "")
                if isinstance(name, str):
                    try:
                        self.phone.dash.set_in_use(name)
                    except ValueError:
                        bad = True
                else:
                    bad = True
            else:
                bad = True
            if not bad:
                snap = self.phone.state()
        if bad:
            self._json({"error": "unknown action" if a not in {"battery","temp","call","hang_up","send","receive","open","visible","sim","tick","charge","goal","undo","in_use","explain","ask","plan","restart_helper"} else "bad request"}, 400)
            return
        self._json(snap)
class _Limits:
    """Per-action rate limits."""
    def __init__(self):
        self._buckets = {"post": secure.RateLimiter(20.0, 40), "send": secure.RateLimiter(0.2, 5), "call": secure.RateLimiter(0.2, 5), "receive": secure.RateLimiter(2.0, 20), "explain": secure.RateLimiter(0.2, 3), "ask": secure.RateLimiter(0.2, 3), "plan": secure.RateLimiter(0.2, 3), "restart_helper": secure.RateLimiter(0.2, 3)}
    def allow(self, key):
        """Delegate key to its bucket."""
        b = self._buckets.get(key)
        return b.allow("all") if b is not None else False
def make_server(port, phone, host="127.0.0.1", token=None):
    """Bind loopback server."""
    secure.check_bind(host)
    class Bound(Handler):
        pass
    Bound.phone = phone
    if token is not None:
        _validate_token(token)
    Bound.token = token if token else secure.new_token()
    Bound.limiter = _Limits()
    Bound.allow_inject = type(phone) is Phone
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
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--visible", action="store_true")
    p.add_argument("--token-file", default=None)
    args = p.parse_args(argv)
    token = None
    if args.token_file is not None:
        import sys
        try:
            with open(args.token_file, encoding="utf-8") as f:
                token = f.read().strip()
            _validate_token(token)
        except Exception:
            print("opos: cannot read session token", file=sys.stderr)
            raise SystemExit(2)
    phone = Phone(visible=_install_helper_visible(args.visible))
    try:
        cfg = setup_config.load()
        if cfg is None:
            cfg = setup_config.default_config()
            cfg["goal"] = {"enabled": True, "time": DEMO_GOAL}
    except Exception:
        cfg = setup_config.default_config()
    goal = cfg["goal"]
    phone.dash.set_goal(goal["time"] if goal["enabled"] else None)
    _route_with_glimmer(phone, os.environ)
    make_server(args.port, phone, token=token).serve_forever()



if __name__ == "__main__":
    main()
