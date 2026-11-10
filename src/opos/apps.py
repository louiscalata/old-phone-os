# SPDX-License-Identifier: GPL-3.0-or-later
"""The everyday-app list. OPOS_APPS names which apps are on. Absent or empty means none.

Each app has a disclosure card and a governor task. The helper is not given a way to write these stores.
"""
import importlib
import os
import re

from opos.governor import Priority, Task
from opos.runtime import Runtime

_ID = re.compile(r"[a-z][a-z0-9-]{0,20}")
# Feature modules register themselves when imported. The map grows as each app lands.
MODULES = {}
_APPS = {}
_BROKEN = set()

_PAUSE = {
    "low": "Paused: the battery is very low.",
    "hot": "Paused: the phone is too hot.",
    "reserve": "Paused: the battery reserve is for calls, messages and alarms.",
    "rules": "Paused to save power.",
    "optional": "Paused to save power.",
    "goal": "Paused to save power.",
    "horizon": "Paused to save power.",
    "glimmer": "Paused to save power.",
    "other": "Paused to save power.",
}


class App:
    """One everyday app. render(token, nonce) returns HTML. get_json and post handle its API."""

    def __init__(self, id, title, path, summary, offline, sends, uses, reality, task_name, priority, cost, render, get_json=None, post=None):
        if _ID.fullmatch(id) is None:
            raise ValueError("bad id")
        if priority not in ("ESSENTIAL", "USEFUL", "OPTIONAL"):
            raise ValueError("bad priority")
        self.id = id
        self.title = title
        self.path = path
        self.summary = summary
        self.offline = offline
        self.sends = sends
        self.uses = uses
        self.reality = reality
        self.task_name = task_name
        self.priority = priority
        self.cost = float(cost)
        self.render = render
        self.get_json = get_json
        self.post = post

    def card(self):
        return "Works offline: %s. Sends data: %s. Uses: %s. Real or practice: %s." % (self.offline, self.sends, self.uses, self.reality)

    def task(self):
        return Task(self.task_name, Priority[self.priority], self.cost)


class Missing:
    """A name in OPOS_APPS with no app behind it."""

    def __init__(self, id):
        self.id = id
        self.title = id[:1].upper() + id[1:]
        self.path = ""
        self.summary = "Not on this phone."
        self.reality = ""

    def card(self):
        return "Not on this phone."


def register(app):
    """Add or replace an app. Tests register a stand-in and then unregister it."""
    if not isinstance(app, App):
        raise TypeError
    _APPS[app.id] = app
    _BROKEN.discard(app.id)
    return app


def unregister(app_id):
    _APPS.pop(app_id, None)
    _BROKEN.discard(app_id)


def names_from(env=None):
    """The app ids in OPOS_APPS, or None when the variable is absent."""
    env = os.environ if env is None else env
    if "OPOS_APPS" not in env:
        return None
    found = []
    for part in (env.get("OPOS_APPS") or "").split(","):
        name = part.strip().lower()
        if _ID.fullmatch(name or "") and name not in found:
            found.append(name)
    return found


def switched_on(env=None):
    """True when OPOS_APPS names at least one id. Absent and empty leave the phone unchanged."""
    return bool(names_from(env))


def load_enabled(env=None):
    """Import each named module once. A module that raises is recorded and does not stop the others."""
    names = names_from(env) or []
    for name in names:
        mod = MODULES.get(name)
        if not mod or name in _APPS or name in _BROKEN:
            continue
        try:
            importlib.import_module(mod)
        except Exception:
            _BROKEN.add(name)


def enabled(env=None):
    """Registered apps named by OPOS_APPS, in the order written."""
    load_enabled(env)
    names = names_from(env) or []
    return [_APPS[name] for name in names if name in _APPS]


def rows(env=None):
    """Index rows, A to Z by title: a real app, or a missing-name line."""
    load_enabled(env)
    names = names_from(env) or []
    out = []
    for name in names:
        out.append(_APPS[name] if name in _APPS else Missing(name))
    out.sort(key=lambda item: item.title.lower())
    return out


def by_path(path):
    for app in _APPS.values():
        if app.path == path:
            return app
    return None


def by_id(app_id):
    return _APPS.get(app_id)


def pause_reason(app_id, level, charging=False, health=1.0, temp_c=25.0, in_use=False):
    """None when the app may run, otherwise one sentence. Alarms stay allowed: the task name is in the keep set."""
    app = _APPS.get(app_id)
    if app is None:
        return "Not on this phone."
    runtime = Runtime(tasks=(app.task(),))
    tick = runtime.step(0, level, charging=charging, health=health, temp_c=temp_c, in_use=app.task_name if in_use else None)
    if app.task_name in tick.decision.allowed:
        return None
    cause = dict(tick.causes).get(app.task_name, "other")
    return _PAUSE.get(cause, _PAUSE["other"])
