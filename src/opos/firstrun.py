# SPDX-License-Identifier: GPL-3.0-or-later
"""First run on the Duo OS: the setup page, the time zone page, and the flag that says setup was shown.

Everything here is off unless the server runs with OPOS_FIRSTRUN=1 (opos-server.service on the Duo OS sets it). With the
variable unset opos.web behaves exactly as before: install() does nothing and no route, header or byte changes.

With it set, opos.web.main() calls install(), which
  * applies the owner's saved time zone before the clock is first read (see opos.timezone);
  * sends a browser that opens "/" to /firstrun until <config>/firstrun.json exists. Only a request that accepts
    text/html is sent: scripts that probe "/" (the kiosk's wait loop, health checks) still get the home page, status 200;
  * adds /firstrun, /timezone and their /api/ routes, and a "Time zone" link in Settings.

Setup can never block the phone: Skip setup, "Open Home" and /?firstrun=later all reach the home screen; if the config
directory cannot be written there is no first run at all; and a failure while deciding falls through to the home page.
"""
import contextlib
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.parse
from html import escape
from pathlib import Path

from opos import clockcheck, firstrun_page, secure, timezone
from opos.setup import config as setup_config
from opos.setup import prefs

log = logging.getLogger("opos.firstrun")
FLAG = "firstrun.json"
KIOSK_PROFILE = (".cache", "opos-kiosk")  # opos-kiosk starts Chromium with --user-data-dir=$HOME/.cache/opos-kiosk
RESTART_DELAY_S = 1.5
DUO_DEVICE = {"id": "surface-duo-1", "name": "Surface Duo", "source": "manual", "battery_health": None, "form_factor": "dual-screen"}
_GOAL_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_later = False  # "Not now" for this server run


def enabled(env=None):
    return (os.environ if env is None else env).get("OPOS_FIRSTRUN") == "1"


def flag_path(env=None):
    return setup_config.config_dir(env) / FLAG


def done(env=None):
    return flag_path(env).is_file()


def mark_done(skipped=False, env=None):
    """Write the flag (atomic). Returns False when it could not be written."""
    path = flag_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            path.parent.chmod(0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "skipped": bool(skipped)}, f)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError:
        return False
    return True


def _writable(env=None):
    d = setup_config.config_dir(env)
    try:
        d.mkdir(parents=True, exist_ok=True)
        return os.access(d, os.W_OK)
    except OSError:
        return False


def defer():
    """Not now: no redirect to the setup page until the server restarts."""
    global _later
    _later = True


def reset():
    global _later
    _later = False


def pending(env=None):
    """True when the first run should be offered: enabled, not done, not deferred, and the answers could be kept."""
    return enabled(env) and not _later and not done(env) and _writable(env)


def clock_info(now=None):
    """The phone's clock for the setup pages: whether it is plausible, and a plain sentence when it is not."""
    t = time.time() if now is None else now
    plausible = clockcheck.clock_plausible(t)
    try:
        year = time.gmtime(t).tm_year
        text = time.strftime("%a %d %b %Y, %H:%M", time.localtime(t))
    except (OverflowError, OSError, ValueError, TypeError):
        year, text = None, ""
    if plausible:
        note = ""
    elif year is None:
        note = "The phone's clock could not be read."
    else:
        note = f"The phone's clock reads {year}, which is almost certainly wrong, so the time and the daily goal will be off. It should correct itself once the phone is online."
    return {"plausible": plausible, "year": year, "text": text, "note": note}


def _goal_now(env=None):
    try:
        cfg = setup_config.load(setup_config.config_path(env))
    except setup_config.ConfigError:
        cfg = None
    g = (cfg or setup_config.default_config())["goal"]
    return {"enabled": bool(g["enabled"]), "time": g["time"]}


def _save_goal(enabled_, hhmm, env=None):
    path = setup_config.config_path(env)
    try:
        cfg = setup_config.load(path)
    except setup_config.ConfigError:
        cfg = None
    if cfg is None:
        cfg = setup_config.default_config()
        cfg["device"] = dict(DUO_DEVICE)
    cfg["goal"] = {"enabled": enabled_, "time": hhmm}
    cfg["applied"] = True
    setup_config.save(cfg, path)


def timezone_state(env=None):
    names, source = timezone.zones()
    return {"zones": names, "source": source, "saved": timezone.load_setting(env), "active": os.environ.get("TZ", ""), "clock": clock_info()}


def state(phone=None, env=None):
    return {"pending": pending(env), "clock": clock_info(), "goal": _goal_now(env), "helper": "on" if getattr(phone, "visible", False) else "off",
            "timezone": {"saved": timezone.load_setting(env)}}


def apply_answers(data, phone=None, lock=None, env=None):
    """Save and apply the choices in data (zone, goal, helper; a missing or null key is left alone).

    Returns (saved, errors): what was kept, and a short name for each choice that was refused or could not be written.
    """
    saved, errors = {}, []

    def live(fn):
        if phone is not None:
            with (lock if lock is not None else contextlib.nullcontext()):
                fn()

    if data.get("zone") is not None:
        try:
            saved["timezone"] = timezone.save_setting(data["zone"], env)
            timezone.apply(saved["timezone"])
        except (ValueError, OSError):
            saved.pop("timezone", None)
            errors.append("time zone")
    g = data.get("goal")
    if g is not None:
        if isinstance(g, dict) and type(g.get("enabled")) is bool and isinstance(g.get("time"), str) and _GOAL_TIME.fullmatch(g["time"]):
            try:
                _save_goal(g["enabled"], g["time"], env)
                live(lambda: phone.dash.set_goal(g["time"] if g["enabled"] else None, phone.clock_s))
                saved["goal"] = {"enabled": g["enabled"], "time": g["time"]}
            except (ValueError, OSError, setup_config.ConfigError):
                errors.append("daily goal")
        else:
            errors.append("daily goal")
    h = data.get("helper")
    if h is not None:
        if h in ("on", "off"):
            try:
                path = prefs.prefs_path(env)
                prefs.answer("helper", h, path=path)
                prefs.finish(path=path)
                live(lambda: phone.set_visible(h == "on"))
                saved["helper"] = h
            except (ValueError, OSError):
                errors.append("helper")
        else:
            errors.append("helper")
    return saved, errors


def _ere(s):
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", s)


def restart_kiosk(env=None, run=subprocess.run):
    """SIGTERM the kiosk's Chromium (found by its profile directory) so opos-kiosk starts it again with the saved TZ.

    Fixed argument list, no shell, nothing from a request in it. Returns whether a process was signalled.
    """
    env = os.environ if env is None else env
    profile = os.path.join(env.get("HOME") or str(Path.home()), *KIOSK_PROFILE)
    argv = ["pkill", "-TERM", "-f", "--", "--user-data-dir=" + _ere(profile) + "( |$)"]
    if hasattr(os, "getuid"):
        argv[2:2] = ["-u", str(os.getuid())]
    try:
        return run(argv, capture_output=True, timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _restart_wanted(zone, client_tz, env=None):
    """The kiosk's Chromium reads TZ only at its start: restart it when it runs with another zone than the saved one."""
    return (os.environ if env is None else env).get("OPOS_KIOSK_RESTART") != "0" and client_tz != zone


def schedule_restart(delay=RESTART_DELAY_S):
    t = threading.Timer(delay, lambda: restart_kiosk())
    t.daemon = True
    t.start()
    return t


_ANCHOR = '<section aria-labelledby="set-about">'
_LINK_STYLE = "display:inline-block;line-height:48px;padding:0 16px;border:1px solid;color:inherit"


def decorate(html, env=None):
    """Settings gets a "Time zone" link ahead of its About section. A page without that anchor is returned unchanged."""
    if _ANCHOR not in html:
        return html
    zone = timezone.load_setting(env) or "default (" + time.tzname[0] + ")"
    link = ('<section aria-labelledby="set-time"><h3 id="set-time">Date and time</h3><a id="tz-link" href="/timezone" style="'
            + _LINK_STYLE + '">Time zone: ' + escape(zone) + "</a></section>")
    return html.replace(_ANCHOR, link + _ANCHOR, 1)


def _send(h, body, ctype, nonce=None, code=200, location=None):
    h.send_response(code)
    for k, v in secure.security_headers(nonce).items():
        h.send_header(k, v)
    if location:
        h.send_header("Location", location)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(body)


def _host_ok(h):
    if secure.host_ok(h.headers.get("Host"), h.port):
        return True
    h._deny(403, "forbidden", "host")
    return False


def _wants_page(h):
    return "text/html" in (h.headers.get("Accept") or "")


def route_get(h, path):
    """Answer a GET that belongs to this track; False means "not mine, carry on as usual"."""
    if path == "/":
        try:
            if urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query).get("firstrun") == ["later"]:
                defer()
                return False
            go = _wants_page(h) and pending() and secure.host_ok(h.headers.get("Host"), h.port)
        except Exception:
            log.warning("first run check failed; showing home")
            return False
        if go:
            h._disarm()
            _send(h, b"", "text/plain", code=302, location="/firstrun")
        return go
    if path in ("/firstrun", "/timezone"):
        if not _host_ok(h):
            return True
        h._disarm()
        nonce = secrets.token_urlsafe(16)
        make = firstrun_page.firstrun_page if path == "/firstrun" else firstrun_page.timezone_page
        _send(h, make(h.token, nonce).encode(), "text/html; charset=utf-8", nonce)
        return True
    if path in ("/api/firstrun", "/api/timezone"):
        if not _host_ok(h):
            return True
        if not secure.token_ok(h.headers.get("X-OPOS-Token"), h.token):
            h._deny(403, "forbidden", "token")
            return True
        h._disarm()
        h._json(state(getattr(h, "phone", None)) if path == "/api/firstrun" else timezone_state())
        return True
    return False


def _post_timezone(h, data):
    zone = timezone.valid_zone(data.get("zone"))
    if zone is None:
        h._json({"error": "unknown time zone"}, 400)
        return
    try:
        timezone.save_setting(zone)
        timezone.apply(zone)
    except (ValueError, OSError):
        h._json({"error": "could not save"}, 500)
        return
    restart = _restart_wanted(zone, data.get("client_tz"))
    h._json({"ok": True, "zone": zone, "restart": restart})
    if restart:
        schedule_restart()


def _post_firstrun(h, data):
    action = data.get("action")
    if action == "skip":
        flag = mark_done(skipped=True)
        if not flag:
            defer()  # the flag cannot be written: do not send the owner back to setup in a loop
        h._json({"ok": True, "done": flag, "restart": False})
    elif action == "finish":
        saved, errors = apply_answers(data, getattr(h, "phone", None), getattr(h, "lock", None))
        flag = mark_done()
        if not flag:
            defer()
        zone = saved.get("timezone")
        restart = bool(zone) and _restart_wanted(zone, data.get("client_tz"))
        h._json({"ok": not errors and flag, "done": flag, "saved": saved, "errors": errors, "restart": restart})
        if restart:
            schedule_restart()
    else:
        h._json({"error": "unknown action"}, 400)


_POST = {"/api/timezone": _post_timezone, "/api/firstrun": _post_firstrun}


def route_post(h):
    """Answer a POST that belongs to this track with the same checks as /api; False means "not mine"."""
    fn = _POST.get(h.path)
    if fn is None:
        return False
    if not _host_ok(h):
        return True
    for ok, code, msg, why in ((secure.origin_ok(h.headers.get("Origin"), h.port), 403, "forbidden", "origin"),
                               (secure.json_type_ok(h.headers.get("Content-Type")), 415, "unsupported media type", "type"),
                               (secure.token_ok(h.headers.get("X-OPOS-Token"), h.token), 403, "forbidden", "token"),
                               (h.limiter.allow("post"), 429, "too many requests", "rate")):
        if not ok:
            h._deny(code, msg, why)
            return True
    try:
        data = secure.read_json_body(h.rfile, h.headers)
    except secure.BodyError as e:
        h._deny(e.status, e.message, "body")
        return True
    h._disarm()
    fn(h, data)
    return True


def install(web, env=None):
    """Hook this track into the opos.web module. Does nothing, and returns None, unless OPOS_FIRSTRUN=1.

    Call it before the Phone is created: the saved time zone must be in place when the clock is first read. Returns a
    function that puts opos.web back as it was (for tests).
    """
    handler = web.Handler
    if not enabled(env) or getattr(handler, "_firstrun_hooked", False):
        return None
    timezone.apply_saved(env)
    get, post, page = handler._get, handler._post, web.page

    def _get(self):
        if not route_get(self, urllib.parse.urlsplit(self.path).path):
            get(self)

    def _post(self):
        if not route_post(self):
            post(self)

    def _page(token="", nonce="", *, demo=False):
        return decorate(page(token, nonce, demo=demo))

    handler._get, handler._post, web.page, handler._firstrun_hooked = _get, _post, _page, True

    def undo():
        handler._get, handler._post, web.page = get, post, page
        if "_firstrun_hooked" in vars(handler):
            del handler._firstrun_hooked

    return undo
