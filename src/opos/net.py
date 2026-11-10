# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone's network, read honestly and changed only where the system allows it.

Status reads NetworkManager (nmcli), ModemManager (mmcli), rfkill, BlueZ (bluetoothctl) and ip through an
injectable runner with a timeout: a missing tool, a failure or a timeout reads as "unavailable", never as an
exception. Wi-Fi control (scan, join, forget, disconnect, on/off) goes through NetworkManager as the unprivileged
user the server runs as. `nmcli general permissions` is read first, and a refusal by polkit is reported in plain
words instead of failing. A Wi-Fi password travels only on nmcli's standard input (`nmcli --ask`, documented to
prompt for the missing password), never in an argument, so it is not in the process list. It is not kept, not
logged and not put in any state; NetworkManager itself stores the profile. Nothing here is part of /api/state, so
no network name reaches the helper. Calls and texts are still simulated, and every status says so.

    python3 -m opos.net [status|scan|probe]     what OPOS sees (probe: the raw tool output, to refine the parsers)
    python3 -m opos.net join SSID < password    the same join the page does (password on stdin)
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import NamedTuple

from opos.hw.battery import read_radios

NM = "org.freedesktop.NetworkManager."
# What each change needs, as `nmcli general permissions` names the NetworkManager permissions (without NM).
NEEDS = {
    "scan": ("wifi.scan",),
    "join": ("network-control", "settings.modify.system"),
    "connect": ("network-control",),
    "forget": ("settings.modify.system",),
    "disconnect": ("network-control",),
    "power": ("enable-disable-wifi",),
}
ACTIONS = tuple(sorted({NM + p for v in NEEDS.values() for p in v}))
SIMULATED = ("Calls and texts in OPOS are still simulated: this page shows the phone's real radios, "
             "but no real call or text goes through the modem yet.")
READ_S = 6.0      # one read-only tool
BUDGET_S = 15.0   # one whole status pass
JOIN_WAIT_S = 35  # nmcli --wait for a join
JOIN_EXTRA_S = 10  # how long past that OPOS waits for nmcli itself
CACHE_S = 3.0
SCAN_WAIT_S = 3.0
USB_IFACES = ("usb", "rndis", "ncm", "enx")
PROFILE_MAX = 20
SCAN_MAX = 30
MODEM_ON = ("registered", "connected", "searching", "enabled")  # ModemManager states in which the radio is on


class Run(NamedTuple):
    rc: int
    out: str = ""
    err: str = ""
    missing: bool = False
    timed_out: bool = False

    @property
    def ok(self):
        return self.rc == 0


def _env(path=None):
    base = os.environ.get("PATH") or "/usr/bin:/bin"
    # C.UTF-8 keeps nmcli's messages English and its SSIDs unmangled.
    return {"PATH": path if path is not None else base + ":/usr/local/sbin:/usr/sbin:/sbin", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


def _kill(p):
    try:
        os.killpg(p.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _run_tty(argv, timeout, stdin, env):
    """Like a person typing: wait for the prompt on a pseudo terminal, then send stdin. For OPOS_NET_TTY=1 only."""
    import pty
    import select
    import termios
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO  # what is typed is not echoed back into the output
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    try:
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, start_new_session=True)
    except FileNotFoundError:
        os.close(master)
        return Run(127, missing=True)
    except (OSError, ValueError):
        os.close(master)
        return Run(126)
    finally:
        os.close(slave)
    end, buf, sent = time.monotonic() + timeout, b"", False
    try:
        while True:
            left = end - time.monotonic()
            if left <= 0:
                _kill(p)
                p.wait()
                return Run(124, timed_out=True)
            if select.select([master], [], [], min(left, 0.2))[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if not sent and re.search(rb"[:?] ?$", buf):
                    os.write(master, stdin.encode("utf-8"))
                    sent = True
            elif p.poll() is not None:
                break
        rc = p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _kill(p)
        return Run(124, timed_out=True)
    finally:
        os.close(master)
    return Run(rc, buf.decode("utf-8", "replace").replace("\r\n", "\n"))


def run(argv, timeout=READ_S, stdin=None, *, path=None, tty=False):
    """One tool: no shell, a clean environment, a hard timeout. Never raises; missing tools and timeouts are flagged."""
    env = _env(path)
    if tty and stdin is not None:
        return _run_tty(list(argv), timeout, stdin, env)
    try:
        p = subprocess.Popen(list(argv), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except FileNotFoundError:
        return Run(127, missing=True)
    except (OSError, ValueError):
        return Run(126)
    try:
        out, err = p.communicate(stdin, timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        try:
            p.communicate(timeout=2)
        except Exception:
            pass
        return Run(124, timed_out=True)
    except Exception:
        _kill(p)
        return Run(126)
    return Run(p.returncode, out or "", err or "")


def make_runner(path=None, tty=False):
    """A runner that looks tools up in exactly `path` (tests put fake nmcli/mmcli there)."""
    return lambda argv, timeout=READ_S, stdin=None: run(argv, timeout, stdin, path=path, tty=tty)


def split_terse(line):
    """Fields of one `nmcli -t` line: ':' separates; '\\:' and '\\\\' are literal."""
    out, cur, esc = [], [], False
    for ch in line.rstrip("\r\n"):
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _rows(text, n):
    return [f for f in (split_terse(l) for l in text.splitlines() if l.strip()) if len(f) >= n]


def clean(s, n=64):
    return "".join(c for c in str(s) if c.isprintable())[:n]


def _kv(text):
    """`mmcli -K` and `nmcli -t -f X device show` lines: 'key : value' or 'key:value'; '--' is empty."""
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            v = v.strip()
            out[k.strip()] = "" if v == "--" else v
    return out


def _bars(sig):
    return "weak" if sig < 30 else "fair" if sig < 60 else "good"


def check_ssid(ssid):
    """None if usable, else why not (plain words)."""
    if not isinstance(ssid, str) or not ssid:
        return "Choose a network first."
    try:
        too_long = len(ssid.encode("utf-8")) > 32
    except UnicodeEncodeError:  # a lone surrogate (undecodable bytes on a command line)
        return "That network name cannot be used."
    if too_long or ssid != clean(ssid, 64):
        return "That network name cannot be used."
    if ssid.startswith("-"):
        return "OPOS cannot join a network whose name starts with a dash yet."
    return None


def _secret_file(password):
    """A mode 0600 file nmcli can read as `passwd-file`. The caller deletes it."""
    fd, path = tempfile.mkstemp(prefix="opos-wifi-")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("802-11-wireless-security.psk:" + password + "\n")
    return path


def check_password(pw):
    """None if usable as a Wi-Fi password (8 to 63 plain characters, or 64 hex digits), else why not."""
    if not isinstance(pw, str) or not pw:
        return "Type the network's password."
    if len(pw) == 64:
        return None if re.fullmatch(r"[0-9A-Fa-f]{64}", pw) else "A 64-character Wi-Fi password must use only 0-9 and a-f."
    if not 8 <= len(pw) <= 63:
        return "A Wi-Fi password is 8 to 63 characters."
    if any(not 32 <= ord(c) <= 126 for c in pw):
        return "Wi-Fi passwords use plain letters, numbers and symbols (no accents or emoji)."
    return None


def explain(r, secret=None):
    """A failed nmcli run in plain words. The secret, if any, is never in the answer."""
    if r.missing:
        return "NetworkManager's nmcli is not installed on this system."
    if r.timed_out or r.rc == 3:
        return "The network service did not answer in time. Try again."
    raw = (r.err + "\n" + r.out).replace(secret, "***") if secret else r.err + "\n" + r.out
    t = raw.lower()
    for key, words in (
        ("not authorized", "NetworkManager does not allow this change for OPOS on this phone."),
        ("insufficient privileges", "NetworkManager does not allow this change for OPOS on this phone."),
        ("secrets were required", "The password was not accepted. Check it and try again."),
        ("no network with ssid", "That network is not in range, or its name is hidden."),
        ("mismatching ssid", "That network is not in range right now."),
        ("not available because", "That network is not in range right now."),
        ("no suitable device", "No Wi-Fi adapter could be used for that. The network may be out of range."),
        ("property is invalid", "The password is not valid for this network."),
        ("ip configuration could not be reserved", "The network accepted the password but gave this phone no address."),
        ("networkmanager is not running", "The phone's network service (NetworkManager) is not running."),
        ("wi-fi is disabled", "Wi-Fi is switched off. Turn it on first."),
        ("wireless is disabled", "Wi-Fi is switched off. Turn it on first."),
        ("took too long", "The network did not answer in time. It may be too far away, or turned off."),
        ("not allowed immediately", "A scan just ran. Wait a few seconds."),
        ("does not exist", "That network is not saved on this phone."),
        ("could not create nmclient", "The phone's network service (NetworkManager) is not reachable."),
    ):
        if key in t:
            return words
    if r.rc == 8:
        return "The phone's network service (NetworkManager) is not running."
    first = next((l.strip() for l in raw.splitlines() if l.strip()), "")
    return ("The network service refused: " + clean(first.removeprefix("Error:").strip(), 120)) if first else "The network service did not say why."


def _ok(msg, **kw):
    return {"ok": True, "message": msg, **kw}


def _bad(msg, **kw):
    return {"ok": False, "error": msg, **kw}


class _Q:
    """One pass over the tools: a shared time budget, and which tools were missing or too slow."""

    def __init__(self, net, budget):
        self.net, self.end, self.missing, self.slow = net, net._clock() + budget, set(), set()

    def call(self, argv, timeout=READ_S, stdin=None):
        left = self.end - self.net._clock()
        if left <= 0.2:
            self.slow.add(argv[0])
            return Run(124, timed_out=True)
        try:
            r = self.net.runner(list(argv), min(timeout, left), stdin)
        except Exception:
            r = Run(126)
        if r.missing:
            self.missing.add(argv[0])
        if r.timed_out:
            self.slow.add(argv[0])
        return r

    def why(self, tool, name):
        if tool in self.missing:
            return f"{name} is not installed"
        if tool in self.slow:
            return f"{name} did not answer in time"
        return f"{name} did not answer"


class Net:
    def __init__(self, runner=None, root="/", env=None, sleep=time.sleep, clock=time.monotonic, wall=time.time):
        self.runner = runner or self._default_run
        self.root = root
        self.env = os.environ if env is None else env
        self._sleep, self._clock, self._wall = sleep, clock, wall
        self._busy = threading.Lock()
        self._cache = None

    def _default_run(self, argv, timeout=READ_S, stdin=None):
        return run(argv, timeout, stdin, tty=self.env.get("OPOS_NET_TTY") == "1")

    def control_enabled(self):
        """Changes are only for installs that manage the phone's own network (the Duo OS image sets OPOS_NETWORK=1)."""
        return self.env.get("OPOS_NETWORK") == "1"

    # ---- readers: each returns None (or a dict whose state says so) when its tool does not answer ----

    def _general(self, q):
        r = q.call(["nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN-HW,WWAN", "general", "status"])
        rows = _rows(r.out, 7) if r.ok else []
        if not rows or rows[0][0] != "running":
            return None
        f = rows[0]
        return {"state": f[1], "connectivity": f[2], "wifi_hw": f[3] == "enabled", "wifi": f[4] == "enabled", "wwan_hw": f[5] == "enabled", "wwan": f[6] == "enabled"}

    def _devices(self, q):
        r = q.call(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        if not r.ok:
            return None
        return [{"device": f[0], "type": f[1], "state": f[2], "connection": f[3]} for f in _rows(r.out, 4)]

    def _permissions(self, q):
        r = q.call(["nmcli", "-t", "general", "permissions"])
        if not r.ok:
            return None
        return {f[0][len(NM):]: f[1].strip().lower() for f in _rows(r.out, 2) if f[0].startswith(NM)}

    def _rfkill(self, q):
        r = q.call(["rfkill", "list"])
        if not r.ok:
            return None
        rows, cur = [], None
        for line in r.out.splitlines():
            m = re.match(r"\s*\d+:\s*([^:]+):\s*(.+?)\s*$", line)
            if m:
                kind = {"wireless lan": "wlan", "bluetooth": "bluetooth", "wireless wan": "wwan"}.get(m.group(2).lower(), m.group(2).lower())
                cur = {"name": m.group(1), "type": kind, "soft": False, "hard": False}
                rows.append(cur)
                continue
            m = re.match(r"\s*(soft|hard) blocked:\s*(yes|no)", line, re.I)
            if m and cur is not None:
                cur[m.group(1).lower()] = m.group(2).lower() == "yes"
        return rows

    def _ip(self, q):
        r = q.call(["ip", "-br", "addr"])
        if not r.ok:
            return None
        out = {}
        for line in r.out.splitlines():
            p = line.split()
            if len(p) >= 2:
                out[p[0].split("@")[0]] = {"state": p[1], "addrs": p[2:]}
        return out

    def _profiles(self, q):
        """Saved connections: [{name, uuid, type, active}], None if NetworkManager does not answer."""
        r = q.call(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"])
        if not r.ok:
            return None
        return [{"name": f[0], "uuid": f[1], "type": f[2], "active": f[3] not in ("", "--")} for f in _rows(r.out, 4)]  # nmcli may print -- for no device

    def _wifi_profiles(self, q, limit=None):
        """(profiles, complete): the saved Wi-Fi profiles with their SSIDs, or (None, False) if NetworkManager does not list them.
        `read` says whether the SSID was really read from the profile; otherwise "ssid" is the profile's name, a guess. `complete`
        means every profile was listed and every SSID read, so nothing saved for a network can be hiding. `limit` cuts the list and
        is for display only: a cut list is never complete."""
        ps = self._profiles(q)
        if ps is None:
            return None, False
        wifi = [p for p in ps if p["type"] in ("wifi", "802-11-wireless")]
        out = []
        for p in wifi if limit is None else wifi[:limit]:
            r = q.call(["nmcli", "-t", "-f", "802-11-wireless.ssid", "connection", "show", "uuid", p["uuid"]])
            f = split_terse(r.out.splitlines()[0]) if r.ok and r.out.strip() else []
            read = len(f) >= 2 and bool(f[1])
            out.append({**p, "ssid": clean(":".join(f[1:])) if read else p["name"], "read": read})
        return out, len(out) == len(wifi) and all(p["read"] for p in out)

    def _saved_wifi(self, q):
        """Saved Wi-Fi profiles for showing (at most PROFILE_MAX; a profile's name stands in for an SSID that was not read), or None.
        Nothing that deletes or decides what is saved may use this: it is cut. Use _wifi_profiles for that."""
        return self._wifi_profiles(q, PROFILE_MAX)[0]

    def _nm_why(self, q):
        return q.why("nmcli", "NetworkManager") if (q.missing or q.slow) else "NetworkManager is not running or did not answer"

    # ---- status ----

    def status(self, fresh=False):
        """Everything OPOS can say about the radios; never raises. Cached for a few seconds."""
        try:
            now = self._clock()
            if self._cache and not fresh and now - self._cache[0] < CACHE_S:
                return self._cache[1]
            s = self._status()
            self._cache = (self._clock(), s)
            return s
        except Exception:
            return {"ok": False, "error": "OPOS could not read the network state.", "simulated": SIMULATED}

    def _status(self):
        q = _Q(self, BUDGET_S)
        radios = read_radios(self.root)
        gs = self._general(q)
        devs = self._devices(q) if gs else None
        rf = self._rfkill(q)
        ips = self._ip(q)
        profiles = self._profiles(q) if gs else None
        perms = self._permissions(q) if gs else None
        wifi = self._wifi(q, gs, devs, ips, radios)
        modem, sim = self._modem(q)
        bt = self._bluetooth(q, rf, radios)
        s = {"ok": True, "wifi": wifi, "bluetooth": bt, "modem": modem, "sim": sim,
             "data": self._data(gs, profiles, wifi), "airplane": self._airplane(gs, rf, bt, radios, modem), "usb": self._usb(ips, radios),
             "control": self._control(q, gs, perms), "simulated": SIMULATED, "notes": []}
        if self._wall() < 1.6e9:
            s["notes"].append("The phone's clock is not set yet, so secure websites and updates may fail until it is.")
        s["tools_missing"] = sorted(q.missing)
        return s

    def _wifi(self, q, gs, devs, ips, radios):
        w = {"present": True if "wifi" in radios["radios_present"] else None, "enabled": None, "state": "unavailable", "device": None,
             "ssid": None, "signal": None, "security": None, "ip": None, "internet": None}
        if gs is None:
            wl = next((v for k, v in (ips or {}).items() if k.startswith(("wl", "wifi"))), None)
            if wl and wl["addrs"]:
                w["ip"] = wl["addrs"][0].split("/")[0]
            w["summary"] = "Wi-Fi: unknown (" + self._nm_why(q) + ")" + (f"; the adapter has address {w['ip']}" if w["ip"] else "")
            return w
        w["enabled"] = gs["wifi"]
        dev = next((d for d in devs or [] if d["type"] == "wifi"), None)
        if devs is None:
            w["state"], w["summary"] = "unknown", "Wi-Fi: unknown (NetworkManager did not list its devices)"
            return w
        if dev is None:
            if w["present"] is None:
                w["present"] = False
            w["state"] = "absent"
            w["summary"] = "Wi-Fi: NetworkManager lists no Wi-Fi adapter" + (" although the system shows one" if w["present"] else "")
            return w
        w["present"], w["device"] = True, dev["device"]
        st = dev["state"].split(" ")[0]
        if not gs["wifi_hw"]:
            w["state"], w["summary"] = "blocked", "Wi-Fi: blocked by a hardware switch or airplane mode"
        elif not gs["wifi"]:
            w["state"], w["summary"] = "off", "Wi-Fi: off"
        elif st == "connected":
            w.update(self._link(q, dev["device"], dev["connection"]))
            w["internet"] = {"full": True, "limited": False, "none": False, "portal": False}.get(gs["connectivity"])
            tail = {"full": ", NetworkManager reports internet access", "limited": ", but NetworkManager sees no internet", "none": ", but NetworkManager sees no internet",
                    "portal": ", but a sign-in page is in the way"}.get(gs["connectivity"], "")
            sig = f" (signal {w['signal']}%, {_bars(w['signal'])})" if w["signal"] is not None else ""
            w["state"] = "connected"
            w["summary"] = f"Wi-Fi: connected to {w['ssid']}{sig}" + (f", address {w['ip']}" if w["ip"] else "") + tail
        elif st in ("connecting", "disconnecting"):
            w["state"], w["summary"] = st, f"Wi-Fi: {st}..."
        elif st == "disconnected":
            w["state"], w["summary"] = "disconnected", "Wi-Fi: on, not connected"
        elif st == "unmanaged":
            w["state"], w["summary"] = "unmanaged", "Wi-Fi: the adapter is not managed by NetworkManager"
        else:
            w["state"], w["summary"] = "not ready", "Wi-Fi: on, but the adapter is not ready yet"
        return w

    def _link(self, q, dev, conn):
        out = {"ssid": clean(conn) if conn else None, "signal": None, "security": None, "ip": None}
        r = q.call(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", dev, "--rescan", "no"])
        for f in _rows(r.out, 4) if r.ok else []:
            if f[0] == "yes":
                out["ssid"] = clean(f[1]) or out["ssid"]
                out["signal"] = int(f[2]) if f[2].isdigit() else None
                out["security"] = f[3] if f[3] not in ("", "--") else "open"
                break
        r = q.call(["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", dev])
        if r.ok:
            for k, v in _kv(r.out).items():
                if k.startswith("IP4.ADDRESS") and v:
                    out["ip"] = v.split("/")[0]
                    break
        out["ssid"] = out["ssid"] or "a network"
        return out

    def _bluetooth(self, q, rf, radios):
        b = {"present": None, "enabled": None, "state": "unavailable"}
        rows = [x for x in rf or [] if x["type"] == "bluetooth"]
        if rows or "bluetooth" in radios["radios_present"]:
            b["present"] = True
        r = q.call(["bluetoothctl", "show"])
        asked = not (r.missing or r.timed_out)  # BlueZ answered, whatever it said
        blob = r.out + r.err
        m = re.search(r"^\s*Powered:\s*(yes|no)", blob, re.M)
        if m:
            b["present"], b["enabled"] = True, m.group(1) == "yes"
        if any(x["hard"] for x in rows):
            b["state"], b["enabled"], s = "blocked", False, "Bluetooth: blocked by a hardware switch"
        elif any(x["soft"] for x in rows):
            b["state"], b["enabled"], s = "off", False, "Bluetooth: off (blocked in software, for example by airplane mode)"
        elif m:
            b["state"], s = ("on" if b["enabled"] else "off"), "Bluetooth: " + ("on" if b["enabled"] else "off")
        elif b["present"] and asked and "Controller " not in blob:
            b["state"], s = "no controller", "Bluetooth: the radio is there, but the Bluetooth service sees no controller yet"
        elif b["present"]:
            b["state"], s = "unknown", "Bluetooth: present; whether it is switched on is not known (" + q.why("bluetoothctl", "the Bluetooth tool") + ")"
        elif asked and "No default controller" in blob:
            b["present"], b["state"], s = False, "absent", "Bluetooth: no adapter found"
        else:
            s = "Bluetooth: unavailable (" + q.why("bluetoothctl", "the Bluetooth tool") + ")"
        b["summary"] = s
        return b

    def _modem(self, q):
        m = {"present": None, "state": "unavailable", "operator": None, "tech": None, "signal": None}
        sim = {"present": None, "locked": None, "state": "unavailable"}
        r = q.call(["mmcli", "-K", "-m", "any"], timeout=8)
        blob = (r.err + r.out).lower()
        if not r.ok:
            if r.missing or r.timed_out:
                m["summary"] = "Modem: unavailable (" + q.why("mmcli", "ModemManager's mmcli") + ")"
            elif "find modem" in blob:
                m["present"], m["state"], m["summary"] = False, "absent", "Modem: ModemManager sees no modem"
            else:
                m["summary"] = "Modem: unavailable (ModemManager is not answering)"
            sim["summary"] = "SIM card: unknown (the modem is not readable)"
            return m, sim
        k = _kv(r.out)
        st = k.get("modem.generic.state", "unknown")
        why = k.get("modem.generic.state-failed-reason", "")
        op = clean(k.get("modem.3gpp.operator-name", ""), 40) or None
        tech = next((v for key, v in k.items() if key.startswith("modem.generic.access-technologies.value") and v), None)
        sg = k.get("modem.generic.signal-quality.value", "")
        m.update(present=True, state=st, operator=op, tech=tech, signal=int(sg) if sg.isdigit() else None)
        words = {"registered": "registered" + (f" on {op}" if op else ""), "connected": "connected (a data session is open)",
                 "searching": "searching for a network", "enabled": "on, not registered", "disabled": "switched off", "locked": "locked by the SIM",
                 "failed": "failed" + (f" ({why})" if why else ""), "initializing": "starting", "unknown": "state not known"}
        extra = (f", {tech.upper()}" if tech else "") + (f", signal {m['signal']}%" if m["signal"] is not None else "")
        m["summary"] = "Modem: " + words.get(st, st) + (extra if st in ("registered", "connected") else "")
        path = k.get("modem.generic.sim", "")
        lock = k.get("modem.generic.unlock-required", "")
        if not path and st in ("initializing", "unknown") and why != "sim-missing":
            sim.update(summary="SIM card: not known yet (the modem is still starting)")
        elif why == "sim-missing" or not path:
            sim.update(present=False, locked=False, state="absent", summary="SIM card: none found")
        elif lock and lock not in ("none", "--"):
            sim.update(present=True, locked=True, state="locked", summary="SIM card: present but locked (it needs a PIN, which OPOS cannot enter yet)")
        else:
            sim.update(present=True, locked=False, state="present", summary="SIM card: present")
        return m, sim

    def _data(self, gs, profiles, wifi):
        d = {"configured": None, "on": None, "state": "unknown"}
        if profiles is None or gs is None:
            d["summary"] = "Mobile data: unknown (NetworkManager is not answering)"
            return d
        gsm = [p for p in profiles if p["type"] == "gsm"]
        d["configured"], d["on"] = bool(gsm), any(p["active"] for p in gsm)
        if d["on"]:
            d["state"], d["summary"] = "on", "Mobile data: on"
        elif not gsm:
            d["state"], d["summary"] = "not set up", "Mobile data: not set up (no mobile connection is saved)"
        elif wifi.get("state") == "connected":
            d["state"], d["summary"] = "off", "Mobile data: off while Wi-Fi is connected (this port switches it off on Wi-Fi to save battery)"
        else:
            d["state"], d["summary"] = "off", "Mobile data: off"
        return d

    def _airplane(self, gs, rf, bt=None, radios=None, modem=None):
        """Airplane mode is "every radio is blocked", so it needs every radio's state. Wi-Fi and mobile data come from rfkill or
        NetworkManager's switches, Bluetooth from rfkill or else from what BlueZ said (bt, the Bluetooth status), and the modem,
        when neither of those says anything about it, from ModemManager (modem, the modem status). A radio that exists but whose
        state was not established keeps the answer at unknown: it is never counted as blocked."""
        kinds = [x for x in rf or [] if x["type"] in ("wlan", "bluetooth", "wwan")]
        flags = [x["soft"] or x["hard"] for x in kinds]
        unknown = []
        present = (radios or {}).get("radios_present", ())
        if gs is not None:  # NetworkManager's own switches stand in for radios rfkill does not list
            if not any(x["type"] == "wlan" for x in kinds):
                flags.append(not gs["wifi"] or not gs["wifi_hw"])
            if not any(x["type"] == "wwan" for x in kinds):
                flags.append(not gs["wwan"] or not gs["wwan_hw"])
        if gs is None and "wifi" in present and not any(x["type"] == "wlan" for x in kinds):
            unknown.append("Wi-Fi")  # the system shows an adapter, but neither rfkill nor NetworkManager said whether it is blocked
        if gs is None and not any(x["type"] == "wwan" for x in kinds) and ("wwan" in present or (modem or {}).get("present") is True):
            # a modem the system or ModemManager shows, and nobody else says whether it is blocked: only a modem that is on says so
            if (modem or {}).get("state") in MODEM_ON:
                flags.append(False)
            else:
                unknown.append("mobile data")
        if bt is not None and not any(x["type"] == "bluetooth" for x in kinds):
            if bt.get("enabled") is not None:
                flags.append(not bt["enabled"])
            elif bt.get("present"):
                unknown.append("Bluetooth")
        if not flags and not unknown:
            return {"on": None, "state": "unknown", "summary": "Airplane mode: unknown"}
        if any(not f for f in flags):  # some radio is known to be working, so not every radio is blocked
            state, on = ("partly", None) if any(flags) else ("off", False)
        elif unknown:
            return {"on": None, "state": "unknown", "summary": "Airplane mode: unknown (the state of " + ", ".join(unknown[:-1]) + (" and " if len(unknown) > 1 else "") + unknown[-1] + " could not be read, "
                                                                "so it cannot be said that every radio is blocked)"}
        else:
            state, on = "on", True
        return {"on": on, "state": state, "summary": {"on": "Airplane mode: on (all radios are blocked)", "off": "Airplane mode: off",
                                                       "partly": "Airplane mode: off, but some radios are blocked or switched off"}[state]}

    def _usb(self, ips, radios):
        names = ips if ips is not None else {n: None for n in radios["net_ifaces"]}
        usb = [n for n in sorted(names) if n.startswith(USB_IFACES)]
        if not names:
            return {"present": None, "state": "unavailable", "ip": None, "summary": "USB network: unavailable (the tools that list network adapters did not answer)"}
        if not usb:
            return {"present": False, "state": "absent", "ip": None, "summary": "USB network: no USB network adapter"}
        v = names[usb[0]]
        ip = next((a.split("/")[0] for a in (v["addrs"] if v else []) if ":" not in a), None)
        up = bool(v) and v["state"] in ("UP", "UNKNOWN")
        return {"present": True, "device": usb[0], "ip": ip, "state": "up" if up and ip else "no address",
                "summary": f"USB network: {usb[0]} " + (f"has address {ip}" if ip else "is present, with no address yet")}

    def _can(self, op, perms):
        v = [perms.get(p) for p in NEEDS[op]]
        return "no" if "no" in v else "auth" if "auth" in v else "yes" if all(x == "yes" for x in v) else "unknown"

    def _deny_text(self, verdict):
        if verdict == "auth":
            return ("NetworkManager would ask an administrator before changing Wi-Fi here, and OPOS cannot answer. "
                    "The system image needs a small permission rule (docs/network.md); until then, set up Wi-Fi from a computer.")
        return ("NetworkManager does not let OPOS change Wi-Fi on this phone yet. "
                "The system image needs a small permission rule (docs/network.md); until then, set up Wi-Fi from a computer.")

    def _control(self, q, gs, perms):
        c = {"enabled": self.control_enabled(), "allowed": False, "ops": {}, "reason": ""}
        if not c["enabled"]:
            c["reason"] = "Network changes are switched off on this install. They are on in the Duo OS image."
        elif gs is None:
            c["reason"] = "The phone's network service (NetworkManager) is not available, so Wi-Fi cannot be changed from here."
        elif perms is None:
            c["reason"] = "NetworkManager did not say what OPOS may change, so Wi-Fi changes may be refused."
            c["allowed"] = True
        else:
            c["ops"] = {op: self._can(op, perms) for op in NEEDS}
            worst = next((v for v in ("no", "auth") if v in (c["ops"]["join"], c["ops"]["forget"], c["ops"]["power"])), None)
            c["allowed"] = worst is None
            if worst:
                c["reason"] = self._deny_text(worst)
        return c

    # ---- control: each returns {"ok": bool, "message"|"error": str, ...} and never raises ----

    def _guard(self, fn, *a):
        if not self.control_enabled():
            return _bad("Network changes are switched off on this install. They are on in the Duo OS image.")
        if not self._busy.acquire(blocking=False):
            return _bad("Another network change is still running. Wait a moment, then try again.")
        try:
            self._cache = None
            return fn(*a)
        except Exception:
            return _bad("OPOS could not do that.")
        finally:
            self._busy.release()
            self._cache = None

    def _ready(self, q, op):
        """(gs, error): NetworkManager running, Wi-Fi on, and the permission for `op` not refused."""
        gs = self._general(q)
        if gs is None:
            return None, _bad("The phone's network service (NetworkManager) is not available: " + self._nm_why(q) + ".")
        if op not in ("power", "forget"):
            if not gs["wifi_hw"]:
                return None, _bad("Wi-Fi is blocked by a hardware switch or airplane mode. OPOS cannot lift that.")
            if not gs["wifi"]:
                return None, _bad("Wi-Fi is switched off. Turn it on first.")
        perms = self._permissions(q)
        v = self._can(op, perms) if perms is not None else "unknown"
        if v in ("no", "auth"):
            return None, _bad(self._deny_text(v))
        return gs, None

    def _wifi_dev(self, q):
        devs = self._devices(q) or []
        return next((d["device"] for d in devs if d["type"] == "wifi"), None)

    def scan(self, rescan=True):
        """Visible networks, strongest first; with rescan, ask the adapter to look again first."""
        try:
            q = _Q(self, BUDGET_S + SCAN_WAIT_S)
            note = ""
            if rescan and self.control_enabled():
                gs, err = self._ready(q, "scan")
                if err:
                    return err
                r = q.call(["nmcli", "device", "wifi", "rescan"], timeout=10)
                if r.ok:
                    self._sleep(SCAN_WAIT_S)
                elif "not allowed immediately" in (r.err + r.out).lower():
                    note = "A scan just ran; showing what it found."
                else:
                    return _bad(explain(r))
            elif rescan:
                note = "Scanning is switched off on this install; showing what the adapter already knows."
            return self._networks(q, note)
        except Exception:
            return _bad("OPOS could not list networks.")

    def _networks(self, q, note):
        dev = self._wifi_dev(q)
        r = q.call(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"] + (["ifname", dev] if dev else []) + ["--rescan", "no"], timeout=10)
        if not r.ok:
            return _bad(explain(r))
        saved = self._saved_wifi(q) or []
        known = {p["ssid"] for p in saved}
        best, hidden = {}, 0
        for f in _rows(r.out, 4):
            ssid = clean(f[1], 32)
            if not ssid:
                hidden += 1
                continue
            sig = int(f[2]) if f[2].isdigit() else 0
            sec = f[3] if f[3] not in ("", "--") else "open"
            cur = best.get(ssid)
            if cur is None or sig > cur["signal"] or f[0] == "yes":
                best[ssid] = {"ssid": ssid, "signal": sig, "security": sec, "active": f[0] == "yes" or bool(cur and cur["active"])}
        nets = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))[:SCAN_MAX]
        for n in nets:
            n["secure"] = n["security"] != "open"
            n["known"] = n["ssid"] in known
            n["supported"] = "802.1X" not in n["security"] and "WEP" not in n["security"]
            how = ("connected" if n["active"] else "saved" if n["known"] else "open network" if not n["secure"] else
                   "needs a password" if n["supported"] else "company or school network, not supported yet" if "802.1X" in n["security"] else "old WEP security, not supported")
            n["label"] = f"{how}, signal {n['signal']}%"
        return _ok(note or f"{len(nets)} network{'s' if len(nets) != 1 else ''} found.", networks=nets, hidden=hidden,
                   saved=sorted(known - {n["ssid"] for n in nets}))

    def join(self, ssid, password=None, hidden=False):
        """Join `ssid`. A password replaces any saved entry for it; with none, a saved entry or an open network is used."""
        bad = check_ssid(ssid)
        if bad:
            return _bad(bad)
        if password is not None and not isinstance(password, str):
            return _bad("Type the network's password.")
        password = password or None
        if password is not None:
            bad = check_password(password)
            if bad:
                return _bad(bad)
        return self._guard(self._join, ssid, password, hidden is True)

    def _join(self, ssid, password, hidden):
        q = _Q(self, JOIN_WAIT_S + 60)
        listed, complete = self._wifi_profiles(q)  # all of them: a saved network must not be missed because it is the 21st
        saved = [p for p in listed or [] if p["ssid"] == ssid]
        op = "connect" if saved and not password else "join"
        gs, err = self._ready(q, op)
        if err:
            return err
        dev = self._wifi_dev(q)
        ifn = ["ifname", dev] if dev else []
        wait = ["--colors", "no", "--wait", str(JOIN_WAIT_S)]
        if password:
            # nmcli makes a new profile for this. The old entries stay until the new one is up: a failed join (the network
            # went away, no address, a timeout) must not cost the owner a password that was right.
            # The password is not an argument. OPOS_NET_TTY=1 still types it on a terminal; otherwise it is a mode 0600 file
            # passed to `connection up ... passwd-file`, and that file is removed before this returns.
            if self.env.get("OPOS_NET_TTY") == "1":
                r = q.call(["nmcli"] + wait + ["--ask", "device", "wifi", "connect", ssid] + ifn + (["hidden", "yes"] if hidden else []), JOIN_WAIT_S + JOIN_EXTRA_S, password + "\n")
            else:
                secret = _secret_file(password)
                try:
                    added = q.call(["nmcli", "connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid,
                                    "wifi-sec.key-mgmt", "wpa-psk"] + ifn + (["wifi.hidden", "yes"] if hidden else []))
                    r = added if not added.ok else q.call(["nmcli"] + wait + ["connection", "up", "id", ssid, "passwd-file", secret], JOIN_WAIT_S + JOIN_EXTRA_S)
                finally:
                    try:
                        os.remove(secret)
                    except OSError:
                        pass
        elif saved:
            r = q.call(["nmcli"] + wait + ["connection", "up", "uuid", saved[0]["uuid"]] + ifn, JOIN_WAIT_S + JOIN_EXTRA_S)
        else:
            seen = next((n for n in (self._networks(q, "").get("networks") or []) if n["ssid"] == ssid), None)
            if seen and seen["secure"]:
                return _bad("This network needs a password.")
            r = q.call(["nmcli"] + wait + ["device", "wifi", "connect", ssid] + ifn + (["hidden", "yes"] if hidden else []), JOIN_WAIT_S + JOIN_EXTRA_S)
        if r.ok:
            msg = f"Connected to {ssid}." + (" The password is kept by the phone's network service, not by OPOS." if password else "")
            if password and saved:
                msg += self._drop_replaced(q, [p for p in saved if p["read"]], r)  # an entry matched only by its name may be another network
            return _ok(msg)
        msg = explain(r, password)
        if password:
            msg += self._after_failed_join(q, ssid, listed, saved, complete)
        return _bad(msg)

    def _drop_replaced(self, q, saved, r):
        """After a join with a new password worked: delete the entries it replaced, never the one now in use. Returns a sentence only
        if something was left."""
        m = re.search(r"activated with '([0-9a-fA-F-]{36})'", r.out)
        if m is None:
            return " The older saved entry for this network was kept, because OPOS could not tell which entry is the new one."
        stuck = 0
        for p in saved:
            if p["uuid"].lower() != m.group(1).lower() and not q.call(["nmcli", "connection", "delete", "uuid", p["uuid"]]).ok:
                stuck += 1
        return " The older saved entry for this network could not be removed." if stuck else ""

    def _after_failed_join(self, q, ssid, listed, saved, complete=True):
        """After a failed join with a password: delete what this attempt left behind (profiles that did not exist before), never what
        was saved. Says only what was checked: `complete` is the first listing's, and a profile whose SSID could not be read may be
        a saved entry for this very network that `saved` cannot see, so "Nothing was saved" needs a listing without such a gap."""
        keep = " Your saved entry for this network was kept." if saved else ""
        if listed is None:
            return keep + " OPOS could not check whether the attempt left an entry behind."
        before = {p["uuid"] for p in listed}
        after, _ = self._wifi_profiles(q)
        if after is None:
            return keep + " OPOS could not check whether the attempt left an entry behind."
        unresolved = 0
        for p in [p for p in after if p["uuid"] not in before]:
            if not (p["read"] and p["ssid"] == ssid and q.call(["nmcli", "connection", "delete", "uuid", p["uuid"]]).ok):
                unresolved += 1
        if unresolved:
            return keep + " OPOS could not remove everything the attempt left behind."
        if keep:
            return keep
        if not complete:
            return " OPOS could not read every saved network, so it cannot tell whether " + ssid + " is saved."
        return " Nothing was saved."

    def forget(self, ssid):
        """Delete every saved Wi-Fi entry for `ssid` (this also disconnects it)."""
        bad = check_ssid(ssid)
        return _bad(bad) if bad else self._guard(self._forget, ssid)

    def _forget(self, ssid):
        q = _Q(self, BUDGET_S + 20)
        gs, err = self._ready(q, "forget")
        if err:
            return err
        saved, complete = self._wifi_profiles(q)  # every profile, never the cut list used for showing
        if saved is None:
            return _bad("NetworkManager did not list the saved networks.")
        mine = [p for p in saved if p["read"] and p["ssid"] == ssid]
        if not mine:
            if not complete:
                return _bad("OPOS could not read every saved network, so it cannot tell whether " + ssid + " is saved.")
            return _bad("That network is not saved on this phone.")
        for p in mine:
            r = q.call(["nmcli", "connection", "delete", "uuid", p["uuid"]])
            if not r.ok:
                return _bad(explain(r))
        # Say "gone" only after looking again at every profile.
        again, complete_again = self._wifi_profiles(q)
        if again is None or not complete_again or any(p["read"] and p["ssid"] == ssid for p in again):
            return _bad(f"Removed {len(mine)} saved {'entry' if len(mine) == 1 else 'entries'} for {ssid}, but OPOS could not check every saved network "
                        "afterwards, so a saved password for it may remain. Try again.")
        return _ok(f"Forgot {ssid}. Its password is gone from this phone.")

    def wifi(self, mode):
        """mode: 'on', 'off' (the Wi-Fi radio) or 'disconnect' (drop the current network; it will not rejoin by itself)."""
        if mode not in ("on", "off", "disconnect"):
            return _bad("Choose on, off or disconnect.")
        return self._guard(self._wifi_mode, mode)

    def _wifi_mode(self, mode):
        q = _Q(self, BUDGET_S + 10)
        if mode == "disconnect":
            gs, err = self._ready(q, "disconnect")
            dev = self._wifi_dev(q)
            if err:
                return err
            if not dev:
                return _bad("NetworkManager lists no Wi-Fi adapter.")
            r = q.call(["nmcli", "device", "disconnect", dev], 20)
            return _ok("Disconnected. Wi-Fi stays on, but the phone will not rejoin a network until you join one.") if r.ok else _bad(explain(r))
        gs, err = self._ready(q, "power")
        if err:
            return err
        r = q.call(["nmcli", "radio", "wifi", mode], 15)
        if not r.ok:
            return _bad(explain(r))
        gs = self._general(q)
        if mode == "on" and gs is not None and not gs["wifi_hw"]:
            return _bad("Wi-Fi is on in software, but a hardware switch or airplane mode still blocks it. OPOS cannot lift that.")
        return _ok("Wi-Fi is on." if mode == "on" else "Wi-Fi is off.")


PROBES = (["nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN-HW,WWAN", "general", "status"], ["nmcli", "-t", "general", "permissions"],
          ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"],
          ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no"], ["nmcli", "-t", "device", "show"],
          ["rfkill", "list"], ["ip", "-br", "addr"], ["bluetoothctl", "show"], ["mmcli", "-K", "-m", "any"], ["id"])


def main(argv=None):
    import json
    import sys
    a = list(sys.argv[1:] if argv is None else argv) or ["status"]
    n = Net(env={**os.environ, "OPOS_NETWORK": "1"})  # typing the command is the owner's choice to change things
    if a[0] == "status":
        out = n.status(True)
    elif a[0] == "scan":
        out = n.scan()
    elif a[0] == "join" and len(a) == 2:
        out = n.join(a[1], sys.stdin.readline().rstrip("\n"))
    elif a[0] == "forget" and len(a) == 2:
        out = n.forget(a[1])
    elif a[0] == "wifi" and len(a) == 2:
        out = n.wifi(a[1])
    elif a[0] == "probe":
        print("# raw output: it holds network names, addresses and device identifiers; share it with care")
        for cmd in PROBES:
            r = run(cmd, 8)
            print("$ " + " ".join(cmd) + f"\nrc={r.rc}" + (" (missing)" if r.missing else " (timed out)" if r.timed_out else "") + "\n" + r.out + r.err)
        return 0
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
