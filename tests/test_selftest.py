# SPDX-License-Identifier: GPL-3.0-or-later
"""opos-selftest against fake roots and the real OPOS server, the image wiring, and the PC script deploy/duo-acceptance.ps1.

The fake phone is a temporary directory shaped like /proc, /sys, /etc, /var and /opt, with fake systemctl, journalctl,
wlr-randr, ip, nmcli and timedatectl in a bin directory that the script is told to use (--root, --bin, --url, --now are
its test seams). Nothing here touches a phone.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELFTEST = ROOT / "packaging/duo-os/opos-selftest"
BUILD = ROOT / "packaging/duo-os/build-image.sh"
VERIFY = ROOT / "packaging/duo-os/verify-image.sh"
PS1 = ROOT / "deploy/duo-acceptance.ps1"
NOW = 1794000000  # 2026-11-06T21:20:00Z
USB_NET = "172.16" + ".42.0/24"  # the base's USB subnet, written in parts so the repository holds no private address
USB_ADDR = "198.51.100.1/24"  # a documentation address for the fake gadget interface

FAKE_SYSTEMCTL = """#!/bin/sh
d="$(dirname "$0")/../states"
echo "systemctl $*" >> "$(dirname "$0")/../calls.log"
unit() { if [ -f "$d/$1" ]; then cat "$d/$1"; else printf 'LoadState=not-found\\nActiveState=inactive\\nSubState=dead\\nResult=success\\n'; fi; }
verb=
for a in "$@"; do case "$a" in -*) ;; *) verb=$a; break;; esac; done
case "$verb" in
show) for a in "$@"; do last=$a; done; unit "$last";;
is-system-running) s=$(cat "$d/_system" 2>/dev/null || echo running); echo "$s"; [ "$s" = running ];;
list-units) cat "$d/_failed" 2>/dev/null; exit 0;;
*) echo "fake systemctl: unexpected verb $verb" >&2; exit 99;;
esac
"""
FAKE_JOURNALCTL = """#!/bin/sh
d="$(dirname "$0")/.."
echo "journalctl $*" >> "$d/calls.log"
key=
while [ $# -gt 0 ]; do case "$1" in -u|-t) key=$2; shift;; esac; shift; done
[ -f "$d/journal/_limited" ] && echo "Hint: You are currently not seeing messages from other users and the system." >&2
[ -f "$d/journal/$key" ] && cat "$d/journal/$key"
exit 0
"""
FAKE_WLR = """#!/bin/sh
d="$(dirname "$0")/.."
echo "wlr-randr $*" >> "$d/calls.log"
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR WAYLAND_DISPLAY=$WAYLAND_DISPLAY" > "$d/wlr-env.log"
cat "$d/wlr-randr.out"
exit "$(cat "$d/wlr-randr.rc" 2>/dev/null || echo 0)"
"""
FAKE_NMCLI = """#!/bin/sh
d="$(dirname "$0")/.."
echo "nmcli $*" >> "$d/calls.log"
case "$*" in *permissions*) cat "$d/nmcli-permissions.out" 2>/dev/null;; *) cat "$d/nmcli.out" 2>/dev/null;; esac
exit 0
"""
FAKE_CAT = """#!/bin/sh
d="$(dirname "$0")/.."
echo "%(name)s $*" >> "$d/calls.log"
cat "$d/%(name)s.out" 2>/dev/null
exit 0
"""
WLR_OUT = """HWCOMPOSER-1 "Unknown Unknown (HWCOMPOSER-1)"
  Make: Unknown
  Physical size: 175x114 mm
  Enabled: %(enabled)s
  Modes:
    2784x1800 px, 60.000000 Hz (preferred, current)
  Position: 0,0
  Transform: normal
  Scale: %(scale)s
  Adaptive Sync: disabled
"""
SSHD = """Include /etc/ssh/sshd_config.d/*.conf
KbdInteractiveAuthentication no
UsePAM yes
X11Forwarding yes
Subsystem\tsftp\t/usr/lib/openssh/sftp-server
"""
SSHD_DROPIN = """# password only from the USB link
PasswordAuthentication no
KbdInteractiveAuthentication no
Match Address %s
    PasswordAuthentication yes
""" % USB_NET


def load():
    mod = types.ModuleType("opos_selftest")
    mod.__file__ = str(SELFTEST)
    exec(compile(SELFTEST.read_text(encoding="utf-8"), str(SELFTEST), "exec"), mod.__dict__)
    return mod


ST = load()


class Fake:
    """A temporary directory shaped like a healthy Duo OS phone; tests break one thing at a time."""

    def __init__(self, url="http://127.0.0.1:1/"):
        self.tmp = Path(tempfile.mkdtemp(prefix="opos-selftest-"))
        self.root, self.bin, self.states = self.tmp / "root", self.tmp / "bin", self.tmp / "states"
        for d in (self.root, self.bin, self.states, self.tmp / "journal"):
            d.mkdir()
        self.url = url
        self.uid = 32011
        for name, body in (("systemctl", FAKE_SYSTEMCTL), ("journalctl", FAKE_JOURNALCTL), ("wlr-randr", FAKE_WLR)):
            self.script(name, body)
        for name in ("ip", "timedatectl"):
            self.script(name, FAKE_CAT % {"name": name})
        self.script("nmcli", FAKE_NMCLI)
        self.build()

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- building blocks
    def script(self, name, body):
        f = self.bin / name
        f.write_text(body, encoding="utf-8", newline="\n")
        f.chmod(0o755)

    def put(self, rel, text="", mode=None):
        f = self.root / rel.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
        if mode:
            f.chmod(mode)
        return f

    def rm(self, rel):
        f = self.root / rel.lstrip("/")
        if f.is_dir() and not f.is_symlink():
            shutil.rmtree(f)
        elif f.exists() or f.is_symlink():
            f.unlink()

    def proc(self, pid, argv, comm=None, uid=None, env=None):
        d = self.root / "proc" / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "comm").write_text((comm or os.path.basename(argv[0]))[:15] + "\n")
        uid = self.uid if uid is None else uid
        (d / "status").write_text("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % ((comm or "x"), uid, uid, uid, uid))
        (d / "environ").write_bytes(b"\0".join(("%s=%s" % kv).encode() for kv in (env or {}).items()))

    def kill(self, pid):
        shutil.rmtree(self.root / "proc" / str(pid), ignore_errors=True)

    def unit(self, name, active="active", sub="running", load="loaded", **extra):
        body = "LoadState=%s\nActiveState=%s\nSubState=%s\nResult=success\n" % (load, active, sub)
        body += "".join("%s=%s\n" % kv for kv in extra.items())
        (self.states / name).write_text(body)

    def battery(self, name, cap, status="Discharging", temp=None, typ="Battery", present="1", online=None):
        d = self.root / "sys/class/power_supply" / name
        d.mkdir(parents=True, exist_ok=True)
        for k, v in (("type", typ), ("capacity", cap), ("status", status), ("present", present), ("temp", temp), ("online", online)):
            if v is not None:
                (d / k).write_text("%s\n" % v)

    def backlight(self, name, bl_power=0, brightness=102):
        d = self.root / "sys/class/backlight" / name
        d.mkdir(parents=True, exist_ok=True)
        for k, v in (("bl_power", bl_power), ("brightness", brightness), ("actual_brightness", brightness), ("max_brightness", 255)):
            if v is not None:
                (d / k).write_text("%s\n" % v)

    def wlr(self, enabled="yes", scale="2.000000", rc=0):
        (self.tmp / "wlr-randr.out").write_text(WLR_OUT % {"enabled": enabled, "scale": scale})
        (self.tmp / "wlr-randr.rc").write_text("%d\n" % rc)

    def journal(self, key, text):
        (self.tmp / "journal" / key).write_text(text)

    def answer(self, name, text):
        (self.tmp / (name + ".out")).write_text(text)

    def build(self):
        for u in ("lxc@android.service", "android-service@hwcomposer.service", "opos-server.service", "opos.service", "NetworkManager.service", "sfduo-usb.service"):
            self.unit(u)
        for u in ("phosh.service", "item.service"):
            self.unit(u, "inactive", "dead")
        self.unit("opos-fallback.service", "inactive", "dead", ExecMainStartTimestampMonotonic="0")
        self.proc(100, ["/usr/bin/phoc", "-v", "-C", "/usr/share/phosh/phoc.ini", "-E", "/usr/libexec/opos/opos-kiosk"], "phoc")
        self.proc(110, ["/vendor/bin/hw/android.hardware.graphics.composer@2.4-service"], "android.hardwar", uid=1000)
        self.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", "--ozone-platform=wayland", "--enable-wayland-ime", self.url], "chromium",
                  env={"XDG_RUNTIME_DIR": "/run/user/%d" % self.uid, "WAYLAND_DISPLAY": "wayland-0", "HOME": "/phone-home"})
        self.proc(201, ["/usr/lib/chromium/chromium", "--type=gpu-process", "--ozone-platform=wayland"], "chromium")
        self.proc(202, ["/usr/lib/chromium/chromium", "--type=renderer"], "chromium")
        self.proc(300, SERVER_ARGV, "python3", env=dict(SERVER_ENV))
        for mod in ("osk", "net_page", "firstrun", "timezone"):  # the image was built with the keyboard, the Network page and the first run
            self.put("opt/opos/src/opos/%s.py" % mod, "# the module\n")
        self.put("proc/uptime", "1234.50 4000.00\n")
        self.put("proc/meminfo", "MemTotal:        5800000 kB\nMemFree:          900000 kB\nMemAvailable:    3000000 kB\n")
        self.backlight("panel0-backlight")
        self.backlight("panel1-backlight")
        self.backlight("backlight", bl_power=None, brightness=2048)
        self.battery("bq27742-0", 80, temp=312)
        self.battery("bq27742-1", 84, temp=305)
        self.battery("usb", None, typ="USB", online=0)
        self.wlr()
        self.put("sys/class/net/usb0/operstate", "up\n")
        self.put("sys/class/net/usb0/carrier", "1\n")
        self.answer("ip", "5: usb0    inet %s brd 198.51.100.255 scope global usb0\\       valid_lft forever preferred_lft forever\n" % USB_ADDR)
        self.answer("timedatectl", "Timezone=Europe/Paris\nNTPSynchronized=yes\n")
        self.answer("nmcli-permissions", "".join("org.freedesktop.NetworkManager.%s:yes\n" % a for a in ("enable-disable-wifi", "network-control", "settings.modify.system", "wifi.scan", "reload")))
        self.answer("nmcli", "wlan0:wifi:connected\nwlan0-p2p:wifi-p2p:disconnected\nlo:loopback:unmanaged\n")
        self.put("etc/systemd/journald.conf.d/50-opos-persistent.conf", "[Journal]\nStorage=persistent\nSystemMaxUse=200M\n")
        self.put("var/log/journal/0123abcd/system.journal", "x" * 2048)
        self.put("var/log/opos-boot-diag.txt", "== 2026-11-06T21:19:00+01:00  up 93.52s  kernel 4.14  opos 0123456789ab\n== system state: running\n== failed units\n")  # as opos-diag writes it
        self.put("opt/opos/REVISION", "0123456789abcdef0123456789abcdef01234567\n")
        self.put("etc/passwd", "root:x:0:0:root:/root:/bin/bash\ndroidian:x:32011:32011:Droidian:/phone-home:/bin/bash\n")
        self.put("etc/ssh/sshd_config", SSHD)
        self.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", SSHD_DROPIN)
        self.put("etc/ssh/sshd_config.d/10-sfduo.conf", "PermitRootLogin prohibit-password\n")
        self.put("usr/libexec/opos/opos-kiosk", KIOSK)

    # --- running
    def run(self, *args, now=NOW, url=None, bins=True):
        argv = ["--root", str(self.root), "--url", url or self.url, "--now", str(now)]
        if bins:
            argv += ["--bin", str(self.bin)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ST.main(argv + list(args))
        return Result(rc, buf.getvalue())

    def snapshot(self):
        out = {}
        for p in sorted(self.tmp.rglob("*")):
            if p.name == "calls.log" or p.name == "wlr-env.log":
                continue
            if p.is_file() and not p.is_symlink():
                out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
            else:
                out[str(p)] = ("dir/link", 0, 0)
        return out

    def calls(self):
        f = self.tmp / "calls.log"
        return f.read_text().splitlines() if f.exists() else []


KIOSK = """#!/bin/sh
while :; do
    z=; f=${XDG_CONFIG_HOME:-$HOME/.config}/opos/timezone
    [ -f "$f" ] && IFS= read -r z < "$f"
    case "$z" in '') ;; *) export TZ="$z" ;; esac
    chromium --kiosk
done
"""
SERVER_ARGV = ["/usr/bin/python3", "-m", "opos.web", "--port", "8765", "--live-battery"]
SERVER_ENV = {"OPOS_ON_PHONE": "1", "OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1"}  # what the image's units give the server
LINE = re.compile(r"^(PASS|FAIL|WARN|SKIP)\s{2}(\S+)\s+(.*)$")


class Result:
    def __init__(self, rc, text):
        self.rc, self.text = rc, text
        self.rows = {}
        for ln in text.splitlines():
            m = LINE.match(ln)
            if m:
                self.rows[m.group(2)] = (m.group(1), m.group(3))

    def status(self, cid):
        return self.rows[cid][0] if cid in self.rows else None

    def ev(self, cid):
        return self.rows[cid][1] if cid in self.rows else None

    def by(self, status):
        return sorted(k for k, v in self.rows.items() if v[0] == status)


class Handler(BaseHTTPRequestHandler):
    """A stand-in OPOS server; class attributes decide its answers."""
    page = '<html><head><meta name="opos-token" content="tok123"><title>x</title></head><body>OPOS</body></html>'
    state = {"battery": {"percent": 82}, "charging": False, "temp_c": 31.0, "battery_source": {"source": "sysfs", "health_source": "sysfs"}}
    gate = True
    osk = False
    network = False
    firstrun = False
    pending = False
    zone_saved = None
    zone_active = None
    tz_status = 200  # what /api/timezone answers with, so that route can fail on its own
    tz_raw = None  # the raw body to send instead of the good one
    tz_zones = ["Europe/Berlin", "UTC"]
    tz_source = "system"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def tag(self):
        return '<script src="/osk.js" defer></script>' if self.osk else ""

    def do_GET(self):
        if self.path == "/":
            links = ('<a href="/network">Wi-Fi and radios</a>' if self.network else "") + ('<a href="/timezone">Time zone</a>' if self.firstrun else "")
            return self.send(200, self.page.replace("</body>", links + self.tag() + "</body>"), "text/html")
        if self.osk and self.path in ("/osk.js", "/osk.css"):
            return self.send(200, "x = 1;", "text/javascript")
        if self.network and self.path == "/network":
            return self.send(200, "<html>network" + self.tag() + "</html>", "text/html")
        if self.firstrun and self.path in ("/firstrun", "/timezone"):
            return self.send(200, "<html>" + self.path + self.tag() + "</html>", "text/html")
        if self.firstrun and self.path in ("/api/firstrun", "/api/timezone"):
            if self.headers.get("X-OPOS-Token") != "tok123":
                return self.send(403, '{"error":"forbidden"}')
            if self.path == "/api/firstrun":
                return self.send(200, json.dumps({"pending": self.pending, "clock": {"plausible": True}, "goal": {}, "helper": "off", "timezone": {"saved": self.zone_saved}}))
            if self.tz_raw is not None:
                return self.send(self.tz_status, self.tz_raw)
            return self.send(self.tz_status, json.dumps({"zones": self.tz_zones, "source": self.tz_source, "saved": self.zone_saved,
                                                         "active": self.zone_active or "", "clock": {}}))
        if self.path == "/api/state":
            if self.gate and self.headers.get("X-OPOS-Token") != "tok123":
                return self.send(403, '{"error":"forbidden"}')
            return self.send(200, json.dumps(self.state))
        self.send(404, "{}")


@contextlib.contextmanager
def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def handler_with(**kw):
    return type("H", (Handler,), kw)


MERGED = handler_with(osk=True, network=True, firstrun=True)  # the stand-in for a server started by the merged image's units


class SelftestCase(unittest.TestCase):
    """Each test gets a healthy fake phone and a stand-in server unless it asks otherwise."""

    def setUp(self):
        cm = serve(MERGED)
        self.url = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.f = Fake(self.url)
        self.addCleanup(self.f.cleanup)

    def run_st(self, *a, **k):
        return self.f.run(*a, **k)


class HealthyPhone(SelftestCase):
    def test_every_check_passes_or_is_skipped(self):
        r = self.run_st()
        self.assertEqual(r.rc, 0, r.text)
        self.assertEqual(r.by("FAIL"), [], r.text)
        self.assertEqual(r.by("WARN"), [], r.text)
        self.assertEqual(r.by("SKIP"), ["clock.kiosk-tz", "kiosk.restarts", "ota.updater"], r.text)
        for cid in ("svc.lxc-android", "svc.hwcomposer", "svc.opos-server", "svc.opos", "svc.phosh-off", "svc.item-off", "svc.fallback", "svc.system",
                    "phoc.process", "phoc.no-shell-mode", "gfx.composer", "gfx.output", "gfx.backlight", "kiosk.chromium", "kiosk.gpu",
                    "server.http", "server.state", "server.token-gate", "battery.sysfs", "battery.api-match", "battery.temp",
                    "clock.year", "clock.timezone", "log.journal", "log.diag", "log.opos-errors", "res.disk", "res.mem",
                    "net.usb", "ssh.auth", "net.manager", "net.wifi", "dev.ssh-key", "dev.devtools", "dev.time-drop",
                    "osk.keyboard", "net.page", "net.permissions", "firstrun.flag", "firstrun.routes"):
            self.assertEqual(r.status(cid), "PASS", "%s: %s" % (cid, r.rows.get(cid)))

    def test_output_format_is_one_line_per_check_with_a_summary(self):
        r = self.run_st()
        lines = r.text.splitlines()
        self.assertTrue(lines[0].startswith("# opos-selftest 1  2026-11-06T"), lines[0])
        self.assertIn("opos=0123456789ab", lines[0])
        self.assertIn("read-only", lines[0])
        body = [ln for ln in lines[1:-1]]
        self.assertTrue(body and all(LINE.match(ln) for ln in body), [ln for ln in body if not LINE.match(ln)])
        self.assertRegex(lines[-1], r"^SELFTEST: \d+ PASS, \d+ WARN, \d+ SKIP, 0 FAIL$")
        self.assertEqual(len(body), len(r.rows), "check ids are unique")

    def test_evidence_is_specific(self):
        r = self.run_st()
        self.assertIn("pid 100", r.ev("phoc.process"))
        self.assertIn("panel0-backlight bl_power=0 brightness=102/255", r.ev("gfx.backlight"))
        self.assertIn("HWCOMPOSER-1 enabled 2784x1800 scale 2", r.ev("gfx.output"))
        self.assertIn("gpu process pid 201", r.ev("kiosk.gpu"))
        self.assertIn("battery 82%, charging False, temp_c 31, source sysfs, health_source sysfs", r.ev("server.state"))
        self.assertIn("bq27742-0 80% Discharging; bq27742-1 84% Discharging", r.ev("battery.sysfs"))
        self.assertIn("usb0 %s" % USB_ADDR, r.ev("net.usb"))
        self.assertIn("password", r.ev("ssh.auth").lower())
        self.assertIn(USB_NET, r.ev("ssh.auth"))
        self.assertIn("Europe/Paris", r.ev("clock.timezone"))
        self.assertIn("wlan0 connected", r.ev("net.wifi"))

    def test_only_filters_by_id_prefix(self):
        r = self.run_st("--only", "svc", "--only", "gfx.back")
        self.assertTrue(all(k.startswith(("svc", "gfx.back")) for k in r.rows), r.rows.keys())
        self.assertIn("gfx.backlight", r.rows)
        self.assertEqual(self.run_st("--only", "nothing.here").rc, 2)

    def test_wlr_randr_gets_the_session_environment_from_the_kiosk_chromium(self):
        self.run_st()
        env = (self.f.tmp / "wlr-env.log").read_text().strip()
        self.assertEqual(env, "XDG_RUNTIME_DIR=/run/user/32011 WAYLAND_DISPLAY=wayland-0")

    def test_wlr_randr_falls_back_to_phocs_uid_and_a_wayland_socket(self):
        self.f.kill(200)
        self.f.put("run/user/32011/wayland-1", "")
        self.run_st()
        self.assertEqual((self.f.tmp / "wlr-env.log").read_text().strip(), "XDG_RUNTIME_DIR=/run/user/32011 WAYLAND_DISPLAY=wayland-1")

    def test_it_writes_nothing_and_asks_only_for_status(self):
        before = self.f.snapshot()
        self.run_st()
        self.assertEqual(before, self.f.snapshot())
        verbs = set()
        for ln in self.f.calls():
            parts = ln.split()
            if parts[0] == "systemctl":
                verbs.add(next(p for p in parts[1:] if not p.startswith("-")))
            else:
                verbs.add(parts[0])
        self.assertLessEqual(verbs, {"show", "is-system-running", "list-units", "journalctl", "wlr-randr", "ip", "nmcli", "timedatectl"})
        for ln in self.f.calls():
            self.assertNotRegex(ln, r"--rotate|--vacuum|--flush|--sync|restart|stop |start |enable|disable|--off|--on\b")

    def test_a_clean_run_in_a_subprocess_exits_zero(self):
        p = subprocess.run([sys.executable, "-I", "-B", str(SELFTEST), "--root", str(self.f.root), "--bin", str(self.f.bin), "--url", self.url, "--now", str(NOW)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("0 FAIL", p.stdout)

    def test_exit_status_of_the_process_follows_the_fail_lines(self):
        base = [sys.executable, "-I", "-B", str(SELFTEST), "--root", str(self.f.root), "--bin", str(self.f.bin), "--url", self.url, "--now", str(NOW)]
        self.f.unit("opos.service", "failed", "failed")
        p = subprocess.run(base, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"(?m)^FAIL  svc\.opos ")
        self.assertRegex(p.stdout.splitlines()[-1], r"^SELFTEST: \d+ PASS, \d+ WARN, \d+ SKIP, 1 FAIL$")
        self.assertEqual(subprocess.run(base + ["--only", "nothing.here"], capture_output=True, text=True, timeout=60).returncode, 2)
        self.assertEqual(subprocess.run(base + ["--bogus"], capture_output=True, text=True, timeout=60).returncode, 2)
        self.assertEqual(subprocess.run(base + ["--only", "svc.phosh"], capture_output=True, text=True, timeout=60).returncode, 0)

    def test_help_names_the_seams_and_says_read_only(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            ST.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        for w in ("--root", "--bin", "--url", "--now", "Changes nothing"):
            self.assertIn(w, buf.getvalue())


class Services(SelftestCase):
    def test_required_units_must_be_active(self):
        for cid, unit in (("svc.lxc-android", "lxc@android.service"), ("svc.hwcomposer", "android-service@hwcomposer.service"),
                          ("svc.opos-server", "opos-server.service"), ("svc.opos", "opos.service")):
            with self.subTest(unit):
                self.f.unit(unit, "failed", "failed")
                r = self.run_st()
                self.assertEqual(r.status(cid), "FAIL")
                self.assertIn("failed", r.ev(cid))
                self.assertEqual(r.rc, 1)
                self.f.unit(unit)

    def test_opos_restart_loop_is_a_failure(self):
        self.f.unit("opos.service", "activating", "auto-restart")
        r = self.run_st()
        self.assertEqual(r.status("svc.opos"), "FAIL")
        self.assertIn("auto-restart", r.ev("svc.opos"))

    def test_phosh_or_item_running_next_to_opos_fails(self):
        self.f.unit("phosh.service")
        self.f.unit("item.service", "activating", "start")
        r = self.run_st()
        self.assertEqual(r.status("svc.phosh-off"), "FAIL")
        self.assertEqual(r.status("svc.item-off"), "FAIL")

    def test_a_failed_phosh_is_only_a_warning(self):
        self.f.unit("phosh.service", "failed", "failed")
        self.assertEqual(self.run_st().status("svc.phosh-off"), "WARN")

    def test_a_fallback_that_ran_this_boot_warns(self):
        self.f.unit("opos-fallback.service", "inactive", "dead", ExecMainStartTimestampMonotonic="123456")
        r = self.run_st()
        self.assertEqual(r.status("svc.fallback"), "WARN")
        self.assertIn("OPOS failed", r.ev("svc.fallback"))

    def test_degraded_system_lists_failed_units(self):
        (self.f.states / "_system").write_text("degraded\n")
        (self.f.states / "_failed").write_text("bluebinder.service loaded failed failed Bluebinder\nofono.service loaded failed failed ofono\n")
        r = self.run_st()
        self.assertEqual(r.status("svc.system"), "WARN")
        self.assertIn("bluebinder.service, ofono.service", r.ev("svc.system"))

    def test_maintenance_is_a_failure(self):
        (self.f.states / "_system").write_text("maintenance\n")
        self.assertEqual(self.run_st().status("svc.system"), "FAIL")

    def test_without_systemctl_nothing_is_assumed_up(self):
        (self.f.bin / "systemctl").unlink()
        r = self.run_st()
        for cid in ("svc.lxc-android", "svc.hwcomposer", "svc.opos-server", "svc.opos"):
            self.assertEqual(r.status(cid), "FAIL")
        self.assertEqual(r.rc, 1)


class Compositor(SelftestCase):
    def test_no_phoc_fails_and_skips_the_flag_check(self):
        self.f.kill(100)
        r = self.run_st()
        self.assertEqual(r.status("phoc.process"), "FAIL")
        self.assertEqual(r.status("phoc.no-shell-mode"), "SKIP")
        self.assertEqual(r.status("gfx.output"), "SKIP")

    def test_shell_mode_in_any_spelling_fails(self):
        for argv in (["/usr/bin/phoc", "-S", "-E", "/x"], ["/usr/bin/phoc", "-vS"], ["/usr/bin/phoc", "--shell"], ["/usr/bin/phoc", "-C", "/c.ini", "-SE", "/x"]):
            with self.subTest(argv):
                self.f.proc(100, argv, "phoc")
                r = self.run_st()
                self.assertEqual(r.status("phoc.no-shell-mode"), "FAIL", r.text)
                self.assertEqual(r.status("phoc.process"), "PASS")
                self.assertEqual(r.rc, 1)

    def test_a_path_or_value_containing_S_is_not_shell_mode(self):
        self.f.proc(100, ["/usr/bin/phoc", "-v", "-C", "/etc/Sessions.ini", "-E", "/opt/Stuff/-S"], "phoc")
        self.assertEqual(self.run_st().status("phoc.no-shell-mode"), "PASS")

    def test_a_wrapper_named_after_phoc_is_not_phoc(self):
        self.f.kill(100)
        self.f.proc(90, ["systemd-cat", "-t", "phoc", "dbus-run-session", "--", "/usr/bin/phoc", "-S"], "systemd-cat")
        self.assertEqual(self.run_st().status("phoc.process"), "FAIL")

    def test_two_compositors_fail(self):
        self.f.proc(101, ["/usr/bin/phoc", "-v"], "phoc")
        self.assertEqual(self.run_st().status("phoc.process"), "FAIL")

    def test_composer_process_is_required(self):
        self.f.kill(110)
        self.assertEqual(self.run_st().status("gfx.composer"), "FAIL")
        self.f.proc(110, ["/bin/sh", "/usr/local/sbin/sfduo-composer-watchdog"], "sh")
        self.assertEqual(self.run_st().status("gfx.composer"), "FAIL", "a script that merely names the composer is not the composer")

    def test_disabled_output_fails_and_wrong_geometry_warns(self):
        self.f.wlr(enabled="no")
        r = self.run_st()
        self.assertEqual(r.status("gfx.output"), "FAIL")
        self.assertIn("DISABLED", r.ev("gfx.output"))
        self.f.wlr(scale="1.000000")
        self.assertEqual(self.run_st().status("gfx.output"), "WARN")

    def test_wlr_randr_that_cannot_connect_warns_and_a_missing_one_skips(self):
        self.f.wlr(rc=1)
        self.assertEqual(self.run_st().status("gfx.output"), "WARN")
        (self.f.bin / "wlr-randr").unlink()
        self.assertEqual(self.run_st().status("gfx.output"), "SKIP")

    def test_backlight_off_fails(self):
        self.f.backlight("panel1-backlight", bl_power=4)
        r = self.run_st()
        self.assertEqual(r.status("gfx.backlight"), "FAIL")
        self.assertIn("panel1-backlight bl_power=4", r.ev("gfx.backlight"))

    def test_backlight_dark_or_single_panel_warns_and_none_fails(self):
        self.f.backlight("panel0-backlight", brightness=0)
        self.assertEqual(self.run_st().status("gfx.backlight"), "WARN")
        self.f.backlight("panel0-backlight")
        self.f.rm("sys/class/backlight/panel1-backlight")
        self.assertEqual(self.run_st().status("gfx.backlight"), "WARN")
        self.f.rm("sys/class/backlight")
        self.assertEqual(self.run_st().status("gfx.backlight"), "FAIL")


class Kiosk(SelftestCase):
    def test_no_chromium_fails(self):
        for pid in (200, 201, 202):
            self.f.kill(pid)
        r = self.run_st()
        self.assertEqual(r.status("kiosk.chromium"), "FAIL")
        self.assertEqual(r.status("kiosk.gpu"), "SKIP")

    def test_a_browser_without_kiosk_is_not_the_kiosk(self):
        self.f.proc(200, ["/usr/lib/chromium/chromium", "https://example.invalid/"], "chromium")
        r = self.run_st()
        self.assertEqual(r.status("kiosk.chromium"), "FAIL")
        self.assertIn("without --kiosk", r.ev("kiosk.chromium"))

    def test_a_helper_that_rewrote_its_title_is_still_a_gpu_process(self):
        self.f.rm("proc/201")
        d = self.f.root / "proc/201"
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"/usr/lib/chromium/chromium --type=gpu-process --ozone-platform=wayland\0")
        (d / "comm").write_text("chromium\n")
        self.assertEqual(self.run_st().status("kiosk.gpu"), "PASS")

    def test_gpu_process_is_required(self):
        self.f.kill(201)
        self.assertEqual(self.run_st().status("kiosk.gpu"), "FAIL")

    def test_disable_gpu_fallback_warns(self):
        self.f.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", "--disable-gpu", self.url], "chromium")
        self.assertEqual(self.run_st().status("kiosk.gpu"), "WARN")

    def test_kiosk_restarts_are_counted_when_the_script_logs_them(self):
        self.f.put("usr/libexec/opos/opos-kiosk", KIOSK + "log() { logger -t opos-kiosk \"$*\"; }\n")
        self.assertEqual(self.run_st().status("kiosk.restarts"), "PASS")
        self.f.journal("opos-kiosk", "chromium exited with status 0 after 3s (quick exits in a row: 1); starting it again in 2s\n")
        r = self.run_st()
        self.assertEqual(r.status("kiosk.restarts"), "WARN")
        self.assertIn("exited 1 time", r.ev("kiosk.restarts"))


class Server(unittest.TestCase):
    def check(self, handler, **fake):
        with serve(handler) as url:
            f = Fake(url)
            self.addCleanup(f.cleanup)
            return f.run("--only", "server")

    def test_nothing_listening_fails_all_and_skips_dependents(self):
        f = Fake("http://127.0.0.1:1/")
        self.addCleanup(f.cleanup)
        r = f.run("--only", "server")
        self.assertEqual(r.status("server.http"), "FAIL")
        self.assertEqual(r.status("server.state"), "SKIP")
        self.assertEqual(r.rc, 1)

    def test_page_without_token_fails(self):
        r = self.check(handler_with(page="<html>no token</html>"))
        self.assertEqual(r.status("server.http"), "FAIL")
        self.assertIn("no opos-token", r.ev("server.http"))

    def test_state_missing_the_live_battery_fields_fails(self):
        r = self.check(handler_with(state={"battery": {"percent": 50}, "charging": False, "temp_c": 30.0}))
        self.assertEqual(r.status("server.state"), "FAIL")
        self.assertIn("battery_source", r.ev("server.state"))
        self.assertIn("not on the live battery", r.ev("server.state"))

    def test_wrong_types_fail(self):
        r = self.check(handler_with(state={"battery": {"percent": "50"}, "charging": "no", "temp_c": None, "battery_source": {}}))
        self.assertEqual(r.status("server.state"), "FAIL")
        for w in ("battery.percent", "charging", "temp_c"):
            self.assertIn(w, r.ev("server.state"))

    def test_a_source_other_than_sysfs_fails_and_unmeasured_health_warns(self):
        st = dict(Handler.state, battery_source={"source": "termux-api", "health_source": "assumed"})
        self.assertEqual(self.check(handler_with(state=st)).status("server.state"), "FAIL")
        st = dict(Handler.state, battery_source={"source": "sysfs", "health_source": "assumed"})
        r = self.check(handler_with(state=st))
        self.assertEqual(r.status("server.state"), "WARN")
        self.assertIn("health", r.ev("server.state"))

    def test_an_open_state_endpoint_fails_the_token_gate(self):
        r = self.check(handler_with(gate=False))
        self.assertEqual(r.status("server.token-gate"), "FAIL")
        self.assertEqual(r.status("server.state"), "PASS")

    def test_a_server_error_is_reported_not_raised(self):
        class Boom(Handler):
            def do_GET(self):
                self.send(500, "{}")
        r = self.check(Boom)
        self.assertEqual(r.status("server.http"), "FAIL")
        self.assertIn("500", r.ev("server.http"))

    def test_the_token_never_appears_in_the_output(self):
        r = self.check(Handler)
        self.assertNotIn("tok123", r.text)


class Battery(SelftestCase):
    def test_no_battery_in_sysfs_fails(self):
        self.f.rm("sys/class/power_supply")
        r = self.run_st()
        self.assertEqual(r.status("battery.sysfs"), "FAIL")
        self.assertEqual(r.status("battery.api-match"), "SKIP")

    def test_implausible_capacity_fails(self):
        self.f.battery("bq27742-0", 140)
        r = self.run_st()
        self.assertEqual(r.status("battery.sysfs"), "FAIL")
        self.assertIn("bq27742-0", r.ev("battery.sysfs"))
        self.f.battery("bq27742-0", -1)
        self.assertEqual(self.run_st().status("battery.sysfs"), "FAIL")

    def test_absent_cells_are_ignored(self):
        self.f.battery("bq27742-1", 0, present="0")
        r = self.run_st()
        self.assertEqual(r.status("battery.sysfs"), "PASS")
        self.assertNotIn("bq27742-1", r.ev("battery.sysfs"))

    def test_opos_far_from_sysfs_warns(self):
        self.f.battery("bq27742-0", 40)
        self.f.battery("bq27742-1", 44)
        r = self.run_st()
        self.assertEqual(r.status("battery.api-match"), "WARN")
        self.assertIn("OPOS shows 82%", r.ev("battery.api-match"))

    def test_charging_disagreement_warns(self):
        self.f.battery("bq27742-0", 80, status="Charging")
        r = self.run_st()
        self.assertEqual(r.status("battery.api-match"), "WARN")
        self.assertIn("charging=False", r.ev("battery.api-match"))

    def test_the_default_temperature_is_flagged(self):
        self.f.battery("bq27742-0", 80, temp=450)
        self.assertEqual(self.run_st().status("battery.temp"), "WARN")
        st = dict(Handler.state, temp_c=30.0)
        with serve(handler_with(state=st)) as url:
            f = Fake(url)
            self.addCleanup(f.cleanup)
            f.rm("sys/class/power_supply/bq27742-0/temp")
            f.rm("sys/class/power_supply/bq27742-1/temp")
            r = f.run()
            self.assertEqual(r.status("battery.temp"), "WARN")
            self.assertIn("default", r.ev("battery.temp"))


class Clock(SelftestCase):
    def test_a_1970_clock_warns(self):
        r = self.run_st(now=5)
        self.assertEqual(r.status("clock.year"), "WARN")
        self.assertIn("1970-01-01", r.ev("clock.year"))
        self.assertEqual(r.rc, 0, "a wrong clock is a warning, not a failure")

    def test_a_clock_before_2026_warns_and_2026_passes(self):
        self.assertEqual(self.run_st(now=1767225599).status("clock.year"), "WARN")
        self.assertEqual(self.run_st(now=1767225600).status("clock.year"), "PASS")

    def test_the_images_clock_floor_is_a_guess(self):
        self.f.put("run/opos/clock-floor", "")
        r = self.run_st()
        self.assertEqual(r.status("clock.year"), "WARN")
        self.assertIn("a guess", r.ev("clock.year"))

    def test_a_clock_behind_the_floor_warns(self):
        self.f.put("opt/opos/CLOCK_FLOOR", "%d\n" % (NOW + 3600))
        self.assertEqual(self.run_st().status("clock.year"), "WARN")

    def test_utc_or_no_timezone_warns(self):
        for tz in ("Etc/UTC", "UTC"):
            self.f.answer("timedatectl", "Timezone=%s\nNTPSynchronized=no\n" % tz)
            self.assertEqual(self.run_st().status("clock.timezone"), "WARN", tz)
        self.f.answer("timedatectl", "")
        self.assertEqual(self.run_st().status("clock.timezone"), "WARN")
        self.f.put("etc/timezone", "Europe/Paris\n")
        self.assertEqual(self.run_st().status("clock.timezone"), "PASS", "falls back to /etc/timezone")

    def test_oposs_own_zone_counts_when_the_system_zone_is_utc(self):
        self.f.answer("timedatectl", "Timezone=Etc/UTC\nNTPSynchronized=yes\n")
        self.f.put("phone-home/.config/opos/timezone", "America/New_York\n")
        r = self.run_st()
        self.assertEqual(r.status("clock.timezone"), "PASS")
        self.assertIn("America/New_York (OPOS's own setting; the system zone is Etc/UTC)", r.ev("clock.timezone"))


class Logs(SelftestCase):
    def test_journal_must_be_persistent(self):
        self.f.rm("etc/systemd/journald.conf.d/50-opos-persistent.conf")
        self.assertEqual(self.run_st().status("log.journal"), "FAIL")

    def test_missing_or_empty_journal_directory_fails(self):
        self.f.rm("var/log/journal")
        self.assertEqual(self.run_st().status("log.journal"), "FAIL")
        (self.f.root / "var/log/journal").mkdir()
        self.assertEqual(self.run_st().status("log.journal"), "FAIL")

    def test_diag_dump_missing_fails_but_is_skipped_right_after_boot(self):
        self.f.rm("var/log/opos-boot-diag.txt")
        self.assertEqual(self.run_st().status("log.diag"), "FAIL")
        self.f.put("proc/uptime", "40.0 100.0\n")
        r = self.run_st()
        self.assertEqual(r.status("log.diag"), "SKIP")
        self.assertIn("90 s", r.ev("log.diag"))
        self.f.put("proc/uptime", "250.0 100.0\n")
        self.assertEqual(self.run_st().status("log.diag"), "SKIP", "the dump is due about 90 s after multi-user.target, not after boot")
        self.f.put("proc/uptime", "400.0 100.0\n")
        self.assertEqual(self.run_st().status("log.diag"), "FAIL")

    def test_a_dump_from_an_earlier_boot_is_not_this_boots_evidence(self):
        self.f.put("proc/uptime", "400.0 100.0\n")
        for up in ("5000s", "5000.12s"):  # opos-diag writes the second form, from /proc/uptime
            self.f.put("var/log/opos-boot-diag.txt", "== 2026-11-05T09:00:00+00:00  up %s  kernel 4.14  opos abc\n== system state: running\n" % up)
            r = self.run_st()
            self.assertEqual(r.status("log.diag"), "WARN", up)
            self.assertIn("earlier boot", r.ev("log.diag"))
        self.f.put("var/log/opos-boot-diag.txt", "== 2026-11-05T09:00:00+00:00  up 93.52s  kernel 4.14  opos abc\n== system state: running\n")
        self.assertEqual(self.run_st().status("log.diag"), "PASS", "a dump 93 s into this boot is this boot's")

    def test_diag_evidence_includes_its_system_state(self):
        self.assertIn("system state: running", self.run_st().ev("log.diag"))

    def test_error_lines_from_the_opos_units_warn_with_counts(self):
        self.f.journal("opos-server.service", "Traceback (most recent call last):\nValueError: boom\n")
        self.f.journal("opos.service", "phoc exited\n")
        r = self.run_st()
        self.assertEqual(r.status("log.opos-errors"), "WARN")
        self.assertIn("3 error line(s)", r.ev("log.opos-errors"))
        self.assertIn("opos-server 2", r.ev("log.opos-errors"))
        self.assertEqual(r.rc, 0)

    def test_a_limited_journal_view_is_said_aloud(self):
        self.f.journal("_limited", "")
        r = self.run_st()
        self.assertEqual(r.status("log.opos-errors"), "PASS")
        self.assertIn("limited view", r.ev("log.opos-errors"))

    def test_without_journalctl_the_error_count_is_skipped(self):
        (self.f.bin / "journalctl").unlink()
        self.assertEqual(self.run_st().status("log.opos-errors"), "SKIP")


class Resources(SelftestCase):
    def test_thresholds(self):
        real = os.statvfs
        try:
            for free, want in ((10, "FAIL"), (100, "WARN"), (2000, "PASS")):
                os.statvfs = lambda p, free=free: types.SimpleNamespace(f_bavail=free * 256, f_frsize=4096, f_blocks=10000 * 256)
                self.assertEqual(self.run_st().status("res.disk"), want, free)
        finally:
            os.statvfs = real

    def test_memory_thresholds(self):
        for kb, want in ((100 * 1024, "FAIL"), (400 * 1024, "WARN"), (3000000, "PASS")):
            self.f.put("proc/meminfo", "MemTotal: 5800000 kB\nMemAvailable: %d kB\n" % kb)
            self.assertEqual(self.run_st().status("res.mem"), want, kb)
        self.f.rm("proc/meminfo")
        self.assertEqual(self.run_st().status("res.mem"), "FAIL")


class Network(SelftestCase):
    def test_usb_gadget_down_fails(self):
        self.f.rm("sys/class/net/usb0")
        self.assertEqual(self.run_st().status("net.usb"), "FAIL")
        self.f.put("sys/class/net/rndis0/operstate", "up\n")
        self.assertEqual(self.run_st().status("net.usb"), "PASS", "rndis0 counts too")

    def test_usb_without_an_address_or_with_its_service_down_fails(self):
        self.f.answer("ip", "")
        r = self.run_st()
        self.assertEqual(r.status("net.usb"), "FAIL")
        self.assertIn("no IPv4 address", r.ev("net.usb"))
        self.f.answer("ip", "5: usb0    inet %s scope global usb0\n" % USB_ADDR)
        self.f.unit("sfduo-usb.service", "failed", "failed")
        self.assertEqual(self.run_st().status("net.usb"), "FAIL")

    def test_a_gadget_service_the_image_does_not_have_is_judged_by_the_interface(self):
        self.f.unit("sfduo-usb.service", "inactive", "dead", load="not-found")
        r = self.run_st()
        self.assertEqual(r.status("net.usb"), "PASS", r.ev("net.usb"))
        self.f.rm("sys/class/net/usb0")
        r = self.run_st()
        self.assertEqual(r.status("net.usb"), "WARN", "no gadget service and no interface: the phone may be reached over Wi-Fi")
        self.assertIn("not installed", r.ev("net.usb"))

    def test_network_manager_down_fails_and_absent_skips(self):
        self.f.unit("NetworkManager.service", "inactive", "dead")
        self.assertEqual(self.run_st().status("net.manager"), "FAIL")
        self.f.unit("NetworkManager.service", "inactive", "dead", load="not-found")
        r = self.run_st()
        self.assertEqual((r.status("net.manager"), r.status("net.wifi")), ("SKIP", "SKIP"))

    def test_wifi_device_states(self):
        self.f.answer("nmcli", "wlan0:wifi:disconnected\n")
        self.assertEqual(self.run_st().status("net.wifi"), "PASS")
        self.f.answer("nmcli", "wlan0:wifi:unavailable\n")
        self.assertEqual(self.run_st().status("net.wifi"), "WARN")
        self.f.answer("nmcli", "lo:loopback:unmanaged\n")
        self.assertEqual(self.run_st().status("net.wifi"), "WARN")
        (self.f.bin / "nmcli").unlink()
        self.assertEqual(self.run_st().status("net.wifi"), "SKIP")


class Ssh(SelftestCase):
    def test_password_login_only_from_the_usb_subnet_passes(self):
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "PASS")
        self.assertIn("passwords allowed only for: Address " + USB_NET, r.ev("ssh.auth"))

    def test_an_unset_password_authentication_is_the_openssh_default_and_fails(self):
        self.f.rm("etc/ssh/sshd_config.d/10-item-password-usb-only.conf")
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "FAIL")
        self.assertIn("default", r.ev("ssh.auth"))

    def test_global_yes_fails_and_first_value_wins(self):
        self.f.put("etc/ssh/sshd_config", "PasswordAuthentication yes\n" + SSHD)
        self.assertEqual(self.run_st().status("ssh.auth"), "FAIL", "the main file's line comes before the Include")
        self.f.put("etc/ssh/sshd_config", SSHD + "PasswordAuthentication yes\n")
        self.assertEqual(self.run_st().status("ssh.auth"), "PASS", "the drop-in's no came first, so the later yes does not count")

    def test_keyboard_interactive_empty_passwords_and_root_login_fail(self):
        for extra in ("KbdInteractiveAuthentication yes\n", "PermitEmptyPasswords yes\n", "PermitRootLogin yes\n", "PubkeyAuthentication no\n"):
            with self.subTest(extra):
                self.f.put("etc/ssh/sshd_config", extra + SSHD)
                self.assertEqual(self.run_st().status("ssh.auth"), "FAIL")

    def test_a_password_exception_for_the_whole_internet_warns(self):
        self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", "PasswordAuthentication no\nKbdInteractiveAuthentication no\nMatch all\n  PasswordAuthentication yes\n")
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "WARN")
        self.assertIn("non-private", r.ev("ssh.auth"))
        self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", "PasswordAuthentication no\nKbdInteractiveAuthentication no\nMatch Address 8.8.8.0/24\n  PasswordAuthentication yes\n")
        self.assertEqual(self.run_st().status("ssh.auth"), "WARN")

    def test_a_list_of_addresses_is_private_only_when_every_entry_is(self):
        a, b, w = "10." + "0.1.0/24", "192." + "168.7.0/24", "8.8.8.0/24"  # written in parts so the repository holds no private address
        for cond, want in ((a + ",0.0.0.0/0", "WARN"), ("0.0.0.0/0," + a, "WARN"), (a + "," + w, "WARN"), (w + "," + a, "WARN"),
                           (a + "," + b, "PASS"), (a + ",!10." + "0.1.9", "PASS"), (a + ",not-an-address", "WARN"), ("10." + "0.0.0/8", "WARN"),
                           ("!10." + "0.1.9", "WARN")):
            with self.subTest(cond):
                self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf",
                           "PasswordAuthentication no\nKbdInteractiveAuthentication no\nMatch Address %s\n  PasswordAuthentication yes\n" % cond)
                self.assertEqual(self.run_st().status("ssh.auth"), want)

    def test_an_unreadable_config_fails(self):
        self.f.rm("etc/ssh/sshd_config")
        self.assertEqual(self.run_st().status("ssh.auth"), "FAIL")

    # OpenSSH applies an Include inside a Match block within that block. Reading it as global let the earlier "no" hide a "yes".
    def test_a_password_yes_included_from_a_match_user_block_is_not_global_and_not_missed(self):
        self.f.put("etc/ssh/sshd_config", SSHD + "Match User droidian\n    Include /etc/ssh/extra-pw.conf\n")
        self.f.put("etc/ssh/extra-pw.conf", "PasswordAuthentication yes\n")
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "WARN", r.ev("ssh.auth"))
        self.assertIn("User droidian", r.ev("ssh.auth"))
        self.assertIn("non-private", r.ev("ssh.auth"))

    def test_the_same_include_from_a_private_address_block_is_fine_and_named(self):
        self.f.put("etc/ssh/sshd_config", SSHD + "Match Address %s\n    Include extra-pw.conf\n" % USB_NET)
        self.f.put("etc/ssh/extra-pw.conf", "PasswordAuthentication yes\n")
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "PASS", r.ev("ssh.auth"))
        self.assertIn("passwords allowed only for: Address " + USB_NET, r.ev("ssh.auth"))

    def test_an_include_in_a_match_block_of_a_dropin_and_a_glob(self):
        self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf",
                   "PasswordAuthentication no\nKbdInteractiveAuthentication no\nMatch User droidian\n    Include /etc/ssh/extra.d/*.conf\n")
        self.f.put("etc/ssh/extra.d/a.conf", "# nothing\n")
        self.f.put("etc/ssh/extra.d/b.conf", "KbdInteractiveAuthentication yes\n")
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "WARN", r.ev("ssh.auth"))
        self.assertIn("User droidian", r.ev("ssh.auth"))

    def test_a_match_line_in_an_included_file_ends_with_that_file(self):
        # the included file opens its own block; the lines after the Include are back in the including file's block
        self.f.put("etc/ssh/sshd_config", SSHD + "Match User droidian\n    Include /etc/ssh/inc.conf\n    PasswordAuthentication yes\n")
        self.f.put("etc/ssh/inc.conf", "Match Address %s\n    PermitEmptyPasswords no\n" % USB_NET)
        r = self.run_st()
        self.assertEqual(r.status("ssh.auth"), "WARN", r.ev("ssh.auth"))
        self.assertIn("User droidian", r.ev("ssh.auth"), "the later yes belongs to Match User, not to the Address block of the included file")
        self.assertNotIn("passwords allowed only for: Address " + USB_NET, r.ev("ssh.auth"))

    def test_a_global_include_still_sets_global_values_and_a_loop_ends(self):
        self.f.put("etc/ssh/sshd_config", "Include /etc/ssh/loop.conf\n" + SSHD)
        self.f.put("etc/ssh/loop.conf", "PasswordAuthentication yes\nInclude /etc/ssh/loop.conf\n")
        self.assertEqual(self.run_st().status("ssh.auth"), "FAIL")

    def test_a_list_with_a_public_entry_after_a_private_one_is_not_private(self):
        # Match Address takes a list; looking only at the first entry would call this private
        a = "10." + "0.1.0/24"
        self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf",
                   "PasswordAuthentication no\nKbdInteractiveAuthentication no\nMatch Address %s,8.8.8.0/24\n  PasswordAuthentication yes\n" % a)
        self.assertEqual(self.run_st().status("ssh.auth"), "WARN")


class Updater(SelftestCase):
    def build(self, phase="idle", signers=1, path="active"):
        self.f.put("var/lib/opos-update/status.json", json.dumps({"phase": phase, "message": "ok", "serial": 3}))
        self.f.put("etc/opos/update/allowed_signers", "opos-release ssh-ed25519 AAAAC3Nza\n" * signers)
        self.f.unit("opos-update.path", path, "waiting")
        link = self.f.root / "opt/opos"
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.move(str(link), str(self.f.root / "opt/opos-releases-0"))
            else:
                link.unlink()
        os.symlink("opos-releases/3-0123456789ab", link)

    def test_absent_updater_is_skipped(self):
        self.assertEqual(self.run_st().status("ota.updater"), "SKIP")

    def test_healthy_updater_passes_and_shows_the_release(self):
        self.build()
        r = self.run_st()
        self.assertEqual(r.status("ota.updater"), "PASS", r.rows.get("ota.updater"))
        self.assertIn("/opt/opos -> opos-releases/3-0123456789ab", r.ev("ota.updater"))
        self.assertIn("1 trusted signer", r.ev("ota.updater"))

    def test_states_that_need_attention(self):
        self.build(phase="rolled-back")
        self.assertEqual(self.run_st().status("ota.updater"), "WARN")
        self.build(phase="applying")
        self.assertEqual(self.run_st().status("ota.updater"), "WARN")
        self.build(signers=0)
        r = self.run_st()
        self.assertEqual(r.status("ota.updater"), "WARN")
        self.assertIn("accepts no update", r.ev("ota.updater"))
        self.build(path="inactive")
        self.assertEqual(self.run_st().status("ota.updater"), "FAIL")


class NoLink(MERGED):
    """A server whose Settings has no link to /network."""

    def send(self, code, body, ctype="application/json"):
        super().send(code, body.replace('<a href="/network">Wi-Fi and radios</a>', "") if isinstance(body, str) else body, ctype)


class NoKeyboardOnPages(MERGED):
    """A server that adds the keyboard to the home page only, not to /network."""

    def send(self, code, body, ctype="application/json"):
        super().send(code, body.replace('<script src="/osk.js" defer></script>', "") if self.path != "/" and isinstance(body, str) else body, ctype)


class PieceCase(SelftestCase):
    def server(self, **env):
        """Restart the stand-in server process with only these variables (a unit that does not set the rest)."""
        self.f.proc(300, SERVER_ARGV, "python3", env=env)

    def on(self, handler, **env):
        """A second fake phone in front of another stand-in server; env is the running server's environment."""
        cm = serve(handler)
        url = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        f = Fake(url)
        self.addCleanup(f.cleanup)
        f.proc(300, SERVER_ARGV, "python3", env=env)
        return f


class Keyboard(PieceCase):
    def test_the_merged_image_runs_the_web_keyboard(self):
        r = self.run_st()
        self.assertEqual(r.status("osk.keyboard"), "PASS", r.ev("osk.keyboard"))
        self.assertIn("the server's environment", r.ev("osk.keyboard"))
        self.assertIn("page loads /osk.js: yes", r.ev("osk.keyboard"))

    def test_an_image_without_the_keyboard_skips_when_it_is_unset_or_off(self):
        self.f.rm("opt/opos/src/opos/osk.py")
        self.server()
        self.assertEqual(self.run_st().status("osk.keyboard"), "SKIP")
        self.server(OPOS_OSK="0")
        self.assertEqual(self.run_st().status("osk.keyboard"), "SKIP")

    def test_an_image_that_ships_the_keyboard_must_run_it(self):
        self.server()
        r = self.run_st()
        self.assertEqual(r.status("osk.keyboard"), "FAIL")
        self.assertIn("without OPOS_OSK", r.ev("osk.keyboard"))
        self.assertEqual(r.rc, 1)
        self.server(OPOS_OSK="off")
        r = self.run_st()
        self.assertEqual(r.status("osk.keyboard"), "WARN", "switched off by the owner: allowed, but nothing can be typed by touch")
        self.assertIn("by touch", r.ev("osk.keyboard"))

    def test_a_commented_setting_does_not_count(self):
        self.f.kill(300)
        self.f.put("etc/systemd/system/opos-server.service", "#Environment=OPOS_OSK=1\n")
        self.assertEqual(self.run_st().status("osk.keyboard"), "FAIL", "shipped and set nowhere")
        self.f.rm("opt/opos/src/opos/osk.py")
        self.assertEqual(self.run_st().status("osk.keyboard"), "SKIP")

    def test_the_web_keyboard_must_be_loaded_by_the_page_and_served(self):
        f = self.on(handler_with(osk=False), OPOS_OSK="1")
        r = f.run()
        self.assertEqual(r.status("osk.keyboard"), "FAIL", "the setting is on but this server serves no keyboard")
        self.assertIn("page loads /osk.js: NO", r.ev("osk.keyboard"))
        f = self.on(handler_with(osk=True), OPOS_OSK="web")
        self.assertEqual(f.run().status("osk.keyboard"), "PASS")

    def test_the_setting_is_found_in_a_unit_drop_in_when_no_server_runs(self):
        f = self.on(handler_with(osk=False))
        f.kill(300)
        f.put("etc/systemd/system/opos-server.service.d/20-osk.conf", "[Service]\nEnvironment=OPOS_OSK=1\n")
        r = f.run()
        self.assertEqual(r.status("osk.keyboard"), "FAIL")
        self.assertIn("20-osk.conf", r.ev("osk.keyboard"))

    def test_a_unit_edited_but_not_restarted_is_a_warning(self):
        self.server(OPOS_NETWORK="1")  # started without the keyboard
        self.f.put("etc/systemd/system/opos-server.service.d/20-osk.conf", "[Service]\nEnvironment=OPOS_OSK=1\n")
        r = self.run_st()
        self.assertEqual(r.status("osk.keyboard"), "WARN")
        self.assertIn("started without it", r.ev("osk.keyboard"))

    def test_the_ports_keyboard_needs_its_process(self):
        self.server(OPOS_OSK="stevia")
        self.assertEqual(self.run_st().status("osk.keyboard"), "WARN")
        self.f.proc(400, ["/usr/bin/phosh-osk-stevia"], "phosh-osk-stevi")
        r = self.run_st()
        self.assertEqual(r.status("osk.keyboard"), "PASS")
        self.assertIn("phosh-osk-stevia", r.ev("osk.keyboard"))

    def test_a_dead_server_skips_it(self):
        self.assertEqual(self.run_st(url="http://127.0.0.1:1/").status("osk.keyboard"), "SKIP")


class NetworkPage(PieceCase):
    def test_an_image_without_the_page_skips_when_it_is_not_on(self):
        self.f.rm("opt/opos/src/opos/net_page.py")
        self.server()
        r = self.run_st()
        self.assertEqual((r.status("net.page"), r.status("net.permissions")), ("SKIP", "SKIP"))

    def test_an_image_that_ships_the_page_must_turn_it_on(self):
        self.server(OPOS_OSK="1")
        r = self.run_st()
        self.assertEqual((r.status("net.page"), r.status("net.permissions")), ("FAIL", "SKIP"))
        self.assertIn("without OPOS_NETWORK=1", r.ev("net.page"))
        self.server(OPOS_NETWORK="yes")
        r = self.run_st()
        self.assertEqual(r.status("net.page"), "WARN", "the server honours exactly 1")
        self.assertIn("read-only", r.ev("net.page"))

    def test_on_needs_the_page_the_link_and_the_keyboard_and_reports_the_permissions(self):
        r = self.run_st()
        self.assertEqual(r.status("net.page"), "PASS", r.ev("net.page"))
        self.assertIn("GET /network -> 200", r.ev("net.page"))
        self.assertIn("Settings links to it: yes", r.ev("net.page"))
        self.assertIn("keyboard loaded on it: yes", r.ev("net.page"))
        self.assertEqual(r.status("net.permissions"), "PASS")
        self.assertIn("wifi.scan=yes", r.ev("net.permissions"))

    def test_a_missing_page_link_or_keyboard_on_it_fails(self):
        r = self.on(handler_with(network=False, osk=True), OPOS_NETWORK="1", OPOS_OSK="1").run()
        self.assertEqual(r.status("net.page"), "FAIL", "this stand-in server has no /network")
        r = self.on(NoLink, OPOS_NETWORK="1").run()
        self.assertEqual(r.status("net.page"), "FAIL")
        self.assertIn("Settings links to it: NO", r.ev("net.page"))
        r = self.on(NoKeyboardOnPages, OPOS_NETWORK="1", OPOS_OSK="1").run()
        self.assertEqual(r.status("net.page"), "FAIL", "a password field without a keyboard cannot be filled in")
        self.assertIn("keyboard loaded on it: NO", r.ev("net.page"))
        r = self.on(NoKeyboardOnPages, OPOS_NETWORK="1").run()
        self.assertEqual(r.status("net.page"), "PASS", "without the web keyboard the page does not owe one")

    def test_permissions(self):
        f = self.f
        f.answer("nmcli-permissions", "org.freedesktop.NetworkManager.wifi.scan:auth\norg.freedesktop.NetworkManager.network-control:yes\n")
        r = f.run()
        self.assertEqual(r.status("net.permissions"), "FAIL")
        self.assertIn("wifi.scan=auth", r.ev("net.permissions"))
        self.assertIn("settings.modify.system=?", r.ev("net.permissions"))
        f.answer("nmcli-permissions", "")
        self.assertEqual(f.run().status("net.permissions"), "WARN")
        (f.bin / "nmcli").unlink()
        self.assertEqual(f.run().status("net.permissions"), "SKIP")

    def test_a_unit_edited_but_not_restarted_is_a_warning(self):
        self.server(OPOS_OSK="1")
        self.f.put("etc/systemd/system/opos-server.service.d/20-opos-network.conf", "[Service]\nEnvironment=OPOS_NETWORK=1\n")
        self.assertEqual(self.run_st().status("net.page"), "WARN")


class FirstRun(PieceCase):
    def test_the_merged_image_runs_the_first_run_and_the_time_zone_page(self):
        r = self.run_st()
        self.assertEqual((r.status("firstrun.flag"), r.status("firstrun.routes")), ("PASS", "PASS"), r.text)
        self.assertIn("GET /firstrun -> 200, /timezone -> 200, /api/firstrun -> 200 (no token -> 403)", r.ev("firstrun.routes"))
        self.assertIn("Settings links to /timezone: yes", r.ev("firstrun.routes"))
        self.assertIn("first run done", r.ev("firstrun.routes"))

    def test_a_pending_first_run_is_said_not_failed(self):
        r = self.on(handler_with(osk=True, network=True, firstrun=True, pending=True), **SERVER_ENV).run()
        self.assertEqual(r.status("firstrun.routes"), "PASS")
        self.assertIn("PENDING", r.ev("firstrun.routes"))

    def test_an_image_without_it_skips_and_an_image_with_it_must_turn_it_on(self):
        self.f.rm("opt/opos/src/opos/firstrun.py")
        self.server()
        r = self.run_st()
        self.assertEqual((r.status("firstrun.flag"), r.status("firstrun.routes"), r.status("clock.kiosk-tz")), ("SKIP", "SKIP", "SKIP"))
        self.f.put("opt/opos/src/opos/firstrun.py", "# the module\n")
        r = self.run_st()
        self.assertEqual((r.status("firstrun.flag"), r.status("firstrun.routes")), ("FAIL", "SKIP"))
        self.assertIn("without OPOS_FIRSTRUN=1", r.ev("firstrun.flag"))
        self.server(OPOS_FIRSTRUN="true")
        self.assertEqual(self.run_st().status("firstrun.flag"), "WARN", "the server honours exactly 1")

    def test_broken_routes_fail(self):
        r = self.on(handler_with(osk=True, network=True, firstrun=False), **SERVER_ENV).run()
        self.assertEqual(r.status("firstrun.routes"), "FAIL")
        self.assertIn("GET /firstrun -> 404", r.ev("firstrun.routes"))

    def test_a_failing_timezone_api_fails_the_check_on_its_own(self):
        """The setup page loads /api/firstrun and /api/timezone together, so it cannot open without both. Everything else here is fine."""
        bad = {"a 500": dict(tz_status=500, tz_raw='{"error":"boom"}'), "a 404": dict(tz_status=404, tz_raw="{}"),
               "invalid JSON": dict(tz_raw="<html>oops</html>"), "an empty body": dict(tz_raw=""), "a JSON list": dict(tz_raw="[]"),
               "no zones": dict(tz_zones=[]), "zones that are not names": dict(tz_zones=[1, 2]), "an unknown source": dict(tz_source="guess"),
               "a missing key": dict(tz_raw=json.dumps({"zones": ["UTC"], "source": "system", "saved": None, "clock": {}}))}
        for name, attrs in bad.items():
            with self.subTest(name):
                r = self.on(handler_with(osk=True, network=True, firstrun=True, **attrs), **SERVER_ENV).run()
                self.assertEqual(r.status("firstrun.routes"), "FAIL", r.ev("firstrun.routes"))
                self.assertIn("/api/timezone ->", r.ev("firstrun.routes"))
                self.assertIn("GET /firstrun -> 200, /timezone -> 200, /api/firstrun -> 200 (no token -> 403)", r.ev("firstrun.routes"), "the other routes were fine")
                self.assertEqual(r.rc, 1)
        r = self.on(handler_with(osk=True, network=True, firstrun=True, tz_status=500, tz_raw="{}"), **SERVER_ENV).run()
        self.assertIn("/api/timezone -> 500", r.ev("firstrun.routes"))
        r = self.on(handler_with(osk=True, network=True, firstrun=True, tz_raw="<html>"), **SERVER_ENV).run()
        self.assertIn("/api/timezone -> 200 (not JSON in the shape the setup page needs)", r.ev("firstrun.routes"))

    def test_a_builtin_zone_list_and_a_saved_zone_are_good_answers(self):
        r = self.on(handler_with(osk=True, network=True, firstrun=True, tz_source="builtin", zone_saved="UTC", zone_active="UTC"), **SERVER_ENV).run()
        self.assertEqual(r.status("firstrun.routes"), "PASS", r.ev("firstrun.routes"))
        self.assertIn("/api/timezone -> 200;", r.ev("firstrun.routes"))

    def test_the_server_must_have_applied_the_saved_zone(self):
        f = self.on(handler_with(osk=True, network=True, firstrun=True, zone_saved="Asia/Tokyo", zone_active="Europe/Paris"), **SERVER_ENV)
        r = f.run()
        self.assertEqual(r.status("firstrun.routes"), "WARN")
        self.assertIn("zone saved Asia/Tokyo, server uses Europe/Paris", r.ev("firstrun.routes"))
        f = self.on(handler_with(osk=True, network=True, firstrun=True, zone_saved="Asia/Tokyo", zone_active="Asia/Tokyo"), **SERVER_ENV)
        self.assertEqual(f.run().status("firstrun.routes"), "PASS")

    def test_the_kiosk_chromium_must_run_in_the_saved_zone(self):
        self.f.put("phone-home/.config/opos/timezone", "Asia/Tokyo\n")
        r = self.run_st()
        self.assertEqual(r.status("clock.kiosk-tz"), "WARN")
        self.assertIn("TZ=unset", r.ev("clock.kiosk-tz"))
        self.f.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", "--ozone-platform=wayland", self.url], "chromium",
                    env={"XDG_RUNTIME_DIR": "/run/user/32011", "WAYLAND_DISPLAY": "wayland-0", "TZ": "Europe/Paris"})
        self.assertEqual(self.run_st().status("clock.kiosk-tz"), "WARN")
        self.f.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", "--ozone-platform=wayland", self.url], "chromium",
                    env={"XDG_RUNTIME_DIR": "/run/user/32011", "WAYLAND_DISPLAY": "wayland-0", "TZ": "Asia/Tokyo"})
        r = self.run_st()
        self.assertEqual(r.status("clock.kiosk-tz"), "PASS")
        self.assertEqual(r.status("clock.timezone"), "PASS")
        self.assertIn("Asia/Tokyo", r.ev("clock.timezone"))

    def test_a_kiosk_script_that_does_not_export_the_zone_fails(self):
        self.f.put("usr/libexec/opos/opos-kiosk", "#!/bin/sh\nchromium --kiosk\n")
        r = self.run_st()
        self.assertEqual(r.status("clock.kiosk-tz"), "FAIL")
        self.assertIn("export TZ", r.ev("clock.kiosk-tz"))
        self.assertEqual(r.rc, 1)

    def test_only_firstrun_prefix(self):
        r = self.run_st("--only", "firstrun")
        self.assertEqual(sorted(r.rows), ["firstrun.flag", "firstrun.routes"])


class DevMarkers(SelftestCase):
    def test_an_authorized_key_warns(self):
        self.f.put("phone-home/.ssh/authorized_keys", "ssh-ed25519 AAAA dev@pc\n")
        r = self.run_st()
        self.assertEqual(r.status("dev.ssh-key"), "WARN")
        self.assertIn("1 authorized ssh key", r.ev("dev.ssh-key"))
        self.assertEqual(r.rc, 0, "a dev image is a warning, not a failure")

    def test_devtools_port_and_time_drop_warn(self):
        self.f.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", "--remote-debugging-port=9222", self.url], "chromium")
        self.assertEqual(self.run_st().status("dev.devtools"), "WARN")
        self.f.proc(200, ["/usr/lib/chromium/chromium", "--kiosk", self.url], "chromium")
        self.f.put("etc/default/opos-kiosk", "OPOS_KIOSK_DEBUG_PORT=9222\n")
        self.assertEqual(self.run_st().status("dev.devtools"), "WARN")
        self.f.put("etc/systemd/system/multi-user.target.wants/opos-clock-set.path", "")
        self.assertEqual(self.run_st().status("dev.time-drop"), "WARN")


class Robustness(SelftestCase):
    def test_text_from_the_phone_is_one_line_without_control_characters(self):
        c = ST.Ctx()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            c.emit("WARN", "x.y", "a\x1b[31mred\x00\nsecond line\r\ttab")
        self.assertEqual(buf.getvalue(), "WARN  x.y                    a [31mred second line tab\n")
        self.assertEqual(buf.getvalue().count("\n"), 1)

    def test_a_phoc_without_a_uid_and_no_browser_does_not_crash_the_output_check(self):
        self.f.kill(200)
        self.f.kill(201)
        self.f.kill(202)
        (self.f.root / "proc/100/status").write_text("Name:\tphoc\n")
        r = self.run_st()
        self.assertNotIn("selftest.error", r.rows, r.text)
        self.assertIn(r.status("gfx.output"), ("PASS", "WARN"))

    def test_a_battery_that_is_not_an_object_is_a_failure_of_the_server_check_not_a_crash(self):
        with serve(handler_with(state={"battery": "82", "charging": False, "temp_c": 31.0, "battery_source": {"source": "sysfs", "health_source": "sysfs"}})) as url:
            f = Fake(url)
            self.addCleanup(f.cleanup)
            r = f.run()
            self.assertNotIn("selftest.error", r.rows, r.text)
            self.assertEqual(r.status("server.state"), "FAIL")


class ReadOnlyByConstruction(unittest.TestCase):
    def ctx(self):
        return ST.Ctx(bins=[])

    def test_the_allow_list_refuses_anything_that_could_change_the_phone(self):
        for cmd in (["systemctl", "restart", "opos"], ["systemctl", "start", "x"], ["systemctl", "daemon-reload"], ["systemctl"],
                    ["journalctl", "--rotate"], ["journalctl", "--vacuum-size=1M"], ["journalctl", "--flush"],
                    ["wlr-randr", "--output", "HWCOMPOSER-1", "--off"], ["wlr-randr", "--on"], ["timedatectl", "set-time", "2026-01-01"],
                    ["ip", "addr", "add", "x", "dev", "usb0"], ["ip", "link", "set", "usb0", "down"], ["nmcli", "connection", "up", "x"],
                    ["nmcli", "device", "wifi", "connect", "x"], ["rm", "-rf", "/"], ["sh", "-c", "true"], ["sudo", "true"], ["chvt", "7"]):
            with self.subTest(cmd), self.assertRaises(ST.Refused):
                self.ctx().run(cmd)

    def test_the_allow_list_accepts_the_status_queries_it_uses(self):
        for cmd in (["systemctl", "show", "--property=ActiveState", "x.service"], ["systemctl", "is-system-running"],
                    ["systemctl", "list-units", "--failed", "--no-legend", "--plain"], ["journalctl", "-b", "-p", "err", "-u", "opos.service", "--no-pager"],
                    ["timedatectl", "show", "-p", "Timezone"], ["ip", "-4", "-o", "addr", "show", "dev", "usb0"], ["wlr-randr"],
                    ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], ["nmcli", "-t", "general", "permissions"]):
            with self.subTest(cmd):
                self.assertFalse(self.ctx().run(cmd).found)  # no bin directories: refused is an exception, found-nothing is Res()

    def test_the_source_has_no_way_to_write(self):
        tree = ast.parse(SELFTEST.read_text(encoding="utf-8"))
        bad = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name == "open":
                    modes = [a.value for a in n.args[1:2] if isinstance(a, ast.Constant)] + [k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    if any(set(str(m)) & set("wax+") for m in modes):
                        bad.append("open for writing at line %d" % n.lineno)
                if name == "replace" and getattr(getattr(f, "value", None), "id", "") in ("os", "shutil"):
                    bad.append("os.replace() at line %d" % n.lineno)
                if name in ("write_text", "write_bytes", "remove", "unlink", "rename", "rmdir", "mkdir", "makedirs", "chmod", "chown",
                            "symlink", "link", "truncate", "system", "popen", "rmtree", "move", "copy", "copyfile", "mkfifo", "setuid", "kill", "killpg", "utime"):
                    bad.append("%s() at line %d" % (name, n.lineno))
        self.assertEqual(bad, [])

    def test_the_only_http_method_is_get_and_the_only_subprocess_goes_through_run(self):
        src = SELFTEST.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'method="([A-Z]+)"', src), ["GET"])
        self.assertEqual(len(re.findall(r"subprocess\.run\(", src)), 1)
        self.assertIsNone(re.search(r"\b(POST|PUT|DELETE|PATCH)\b|sudo|shell=True", src))

    def test_the_environment_is_read_only_for_PATH(self):
        tree = ast.parse(SELFTEST.read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "environ":
                names.update(a.value for a in n.args[:1] if isinstance(a, ast.Constant))
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) and n.value.attr == "environ":
                names.add(getattr(n.slice, "value", "?"))
        self.assertLessEqual(names, {"PATH"}, "production runs take their roots from arguments only: %s" % sorted(names))

    def test_an_internal_error_is_a_failure_not_a_pass(self):
        f = Fake()
        self.addCleanup(f.cleanup)
        real = ST.CHECKS
        ST.CHECKS = (lambda c: 1 / 0,)
        try:
            r = f.run()
        finally:
            ST.CHECKS = real
        self.assertEqual(r.status("selftest.error"), "FAIL")
        self.assertEqual(r.rc, 1)


class RealServer(unittest.TestCase):
    """The checks against the real OPOS web server on a fake live battery, so a change to /api/state shows up here."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "src"))
        from opos import web
        from opos.hw.live_battery import LiveBattery
        reading = {"level": 82.0, "temp_c": None, "charging": False, "source": "sysfs", "health": 0.9, "health_source": "sysfs"}
        cls.web = web
        cls.srv = web.make_server(0, web.Phone(live_battery=LiveBattery(reader=lambda: dict(reading))))
        threading.Thread(target=cls.srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_server_and_battery_checks_pass_against_the_real_handler(self):
        f = Fake(self.url)
        self.addCleanup(f.cleanup)
        r = f.run("--only", "server", "--only", "battery")
        for cid in ("server.http", "server.state", "server.token-gate", "battery.sysfs", "battery.api-match", "battery.temp"):
            self.assertEqual(r.status(cid), "PASS", "%s: %s" % (cid, r.rows.get(cid)))
        self.assertIn("battery 82%", r.ev("server.state"))
        self.assertIn("source sysfs", r.ev("server.state"))

    def test_the_demo_server_is_not_accepted_as_the_phone(self):
        srv = self.web.make_server(0, self.web.Phone())
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        f = Fake("http://127.0.0.1:%d/" % srv.server_address[1])
        self.addCleanup(f.cleanup)
        r = f.run("--only", "server")
        self.assertEqual(r.status("server.state"), "FAIL")
        self.assertIn("not on the live battery", r.ev("server.state"))


class ImageWiring(unittest.TestCase):
    def block(self, path):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^# --- qa: BEGIN.*?\n(.*?)^# --- qa: END", text, re.M | re.S)
        self.assertIsNotNone(m, "%s has no qa block" % path.name)
        self.assertEqual(len(re.findall(r"^# --- qa: BEGIN", text, re.M)), 1)
        self.assertEqual(len(re.findall(r"^# --- qa: END", text, re.M)), 1)
        return m.group(1)

    def test_scripts_parse(self):
        for p in (BUILD, VERIFY):
            r = subprocess.run(["sh", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, p.name + ": " + r.stderr)
        ast.parse(SELFTEST.read_text(encoding="utf-8"))

    def test_the_script_is_executable_python_with_a_header(self):
        lines = SELFTEST.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "#!/usr/bin/python3 -IB")
        self.assertEqual(lines[1], "# SPDX-License-Identifier: GPL-3.0-or-later")
        self.assertNotIn(b"\r", SELFTEST.read_bytes())
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "packaging/duo-os/opos-selftest"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            self.assertTrue(r.stdout.startswith("100755"), "commit it executable: git update-index --chmod=+x")

    def test_build_block_installs_the_script_and_a_wrapper(self):
        b = self.block(BUILD)
        self.assertIn('install -m 0755 "$here/opos-selftest" "$r/usr/libexec/opos/opos-selftest"', b)
        self.assertIn('$r/usr/sbin/opos-selftest', b)
        with tempfile.TemporaryDirectory() as d:
            here = Path(d) / "here"
            here.mkdir()
            shutil.copy(SELFTEST, here / "opos-selftest")
            r = Path(d) / "r"
            (r / "usr/libexec/opos").mkdir(parents=True)
            (r / "usr/sbin").mkdir(parents=True)
            p = subprocess.run(["sh", "-eu", "-c", 'r=%s; here=%s\n%s' % (r, here, b)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            wrapper = (r / "usr/sbin/opos-selftest").read_text()
            self.assertIn('exec /usr/libexec/opos/opos-selftest "$@"', wrapper)
            self.assertEqual(subprocess.run(["sh", "-n", str(r / "usr/sbin/opos-selftest")]).returncode, 0)
            self.assertEqual(oct((r / "usr/libexec/opos/opos-selftest").stat().st_mode & 0o777), "0o755")
            self.assertEqual(oct((r / "usr/sbin/opos-selftest").stat().st_mode & 0o777), "0o755")
            self.assertIn("usr/libexec/opos/opos-selftest", p.stdout)
            self.assertIn("usr/sbin/opos-selftest", p.stdout)

    def test_build_block_is_where_a_merge_will_not_collide(self):
        text = BUILD.read_text(encoding="utf-8")
        self.assertLess(text.index("# --- qa: BEGIN"), text.index("# DEV ONLY"))
        self.assertGreater(text.index("# --- qa: BEGIN"), text.index("ln -sf /etc/systemd/system/opos-server.service"))

    def test_verify_block_checks_files_wrapper_and_runs_the_script_with_the_images_python(self):
        v = self.block(VERIFY)
        self.assertIn("opos-selftest", v)
        self.assertIn("--help", v)
        self.assertIn('check "', v)
        self.assertIn("py ", v)
        self.assertGreater(VERIFY.read_text(encoding="utf-8").index("# --- qa: BEGIN"), VERIFY.read_text(encoding="utf-8").index("py()"))


class Acceptance(unittest.TestCase):
    """deploy/duo-acceptance.ps1: its text, because PowerShell is not on the Linux test machines (a parser check runs where it is)."""

    @classmethod
    def setUpClass(cls):
        cls.text = PS1.read_text(encoding="utf-8")
        cls.code = "\n".join(ln for ln in cls.text.splitlines() if not ln.lstrip().startswith("#"))

    def test_header_and_encoding(self):
        raw = PS1.read_bytes()
        self.assertTrue(self.text.startswith("# SPDX-License-Identifier: GPL-3.0-or-later\n"))
        self.assertNotIn(b"\r", raw)
        raw.decode("ascii")  # Windows PowerShell 5.1 would misread other bytes; pwsh would not care
        self.assertIn("#Requires -Version 7", self.text)

    def test_parameters(self):
        m = re.search(r"param\((.*?)\n\)", self.text, re.S)
        self.assertIsNotNone(m)
        p = m.group(1)
        self.assertRegex(p, r"\[Alias\('Host'\)\][^\n]*\$PhoneHost")
        self.assertRegex(p, r"\$Key\b")
        self.assertRegex(p, r"\$OutDir\b")
        self.assertRegex(p, r"\[Parameter\(Mandatory = \$true\)\][^\n]*\$PhoneHost")

    def test_nothing_personal_is_written_into_it(self):
        for pat in (r"[A-Za-z]:\\", r"/home/", r"/Users/", r"\\Users\\", r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])", r"BEGIN [A-Z ]*PRIVATE", r"ssh-(ed25519|rsa) AAAA"):
            with self.subTest(pat):
                hits = [h for h in re.findall(pat, self.text) if h != "127.0.0.1"] if "\\d" in pat else re.findall(pat, self.text)
                self.assertEqual(hits, [])
        for ln in re.findall(r"https?://[^\s'\"<>]+", self.text):
            self.assertRegex(ln, r"^https?://(127\.0\.0\.1|\$)", ln)

    def test_it_runs_only_read_only_commands_on_the_phone(self):
        m = re.search(r"\$Remote = \[ordered\]@\{(.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(m, "the remote commands must live in one $Remote table")
        cmds = dict(re.findall(r"^\s*(\w+)\s*=\s*'([^']*)'\s*$", m.group(1), re.M))
        self.assertGreaterEqual(len(cmds), 3)
        allowed = [r"/usr/sbin/opos-selftest", r"cat /opt/opos/REVISION", r"cat /var/log/opos-boot-diag\.txt", r"test -r /var/log/opos-boot-diag\.txt"]
        for k, v in cmds.items():
            self.assertTrue(any(re.fullmatch(a, v) for a in allowed), "%s = %r is not an allowed read-only command" % (k, v))
            self.assertIsNone(re.search(r"[;&|<>`$()\n]", v), v)
        self.assertEqual(len(re.findall(r"=\s*'", m.group(1))), len(cmds), "every entry in $Remote must be a plain quoted string")
        calls = re.findall(r"(?<!function )Invoke-Ssh\b[^\n]*", self.code)
        self.assertGreaterEqual(len(calls), 4)
        for c in calls:
            self.assertRegex(c, r"Invoke-Ssh\s+\$Remote\.\w+", c)
        for w in (r"\bscp\b", r"\bsftp\b", r"\brsync\b", r"\bsudo\b", r"\bsu\b", r"\brm\b", r"\bmv\b", r"\btee\b", r"\bdd\b", r"\bkill\b", r"\bsystemctl\b", r"\bchmod\b",
                  r"\breboot\b", r"\bapt\b", r"\bwget\b", r"\bcurl\b", r"Invoke-Expression", r"\biex\b"):
            self.assertIsNone(re.search(w, self.code), "forbidden in the PC script: " + w)

    def test_ssh_options_make_it_non_interactive_and_key_only(self):
        for w in ("BatchMode=yes", "PasswordAuthentication=no", "ConnectTimeout=", "StrictHostKeyChecking=accept-new", "UserKnownHostsFile="):
            self.assertIn(w, self.code)

    def test_the_screenshot_path_is_a_tunnel_to_the_loopback_devtools_port(self):
        for w in ("'-N'", "'-L'", "ExitOnForwardFailure=yes", "ClientWebSocket", "Page.captureScreenshot", "/json/list", "89504E470D0A1A0A", "[Convert]::FromBase64String"):
            self.assertIn(w, self.code)
        self.assertRegex(self.code, r"127\.0\.0\.1:\$\{?\w+\}?:127\.0\.0\.1:\$\{?\w+\}?")

    def test_it_writes_a_report_with_hashes_and_exits_nonzero_on_failure(self):
        for w in ("report.md", "Get-FileHash", "SHA256", "/opt/opos/REVISION", "opos-boot-diag.txt", "SHA256SUMS", "exit 1", "exit 2"):
            self.assertIn(w, self.code + self.text)

    def test_braces_and_quotes_balance(self):
        s = re.sub(r"@'.*?'@|@\".*?\"@", "", self.text, flags=re.S)
        s = re.sub(r"<#.*?#>", "", s, flags=re.S)
        out, i, depth = [], 0, {"{": 0, "(": 0, "[": 0}
        pairs = {"}": "{", ")": "(", "]": "["}
        while i < len(s):
            ch = s[i]
            if ch == "#" and (i == 0 or s[i - 1] in " \t\n"):
                i = s.find("\n", i)
                if i < 0:
                    break
                continue
            if ch == "`":
                i += 2
                continue
            if ch == "'":
                i += 1
                while i < len(s) and not (s[i] == "'" and s[i + 1:i + 2] != "'"):
                    i += 2 if s[i] == "'" else 1
                i += 1
                continue
            if ch == '"':
                i += 1
                while i < len(s) and s[i] != '"':
                    i += 2 if s[i] == "`" else 1
                i += 1
                continue
            if ch in depth:
                depth[ch] += 1
            elif ch in pairs:
                depth[pairs[ch]] -= 1
                self.assertGreaterEqual(depth[pairs[ch]], 0, "unbalanced %s near offset %d" % (ch, i))
            i += 1
        self.assertEqual(depth, {"{": 0, "(": 0, "[": 0})

    def test_powershell_parser_accepts_it(self):
        exe = shutil.which("pwsh")
        if not exe:
            self.skipTest("pwsh is not installed here; the lexical checks above stand in")
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "parse.ps1"
            probe.write_text("param($p)\n$t=$null;$e=$null\n[void][System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$t,[ref]$e)\nif($e.Count){$e|ForEach-Object{$_.Message};exit 1}\n", encoding="ascii")
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-File", str(probe), str(PS1)], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)



SELFTEST_OUT = (
    "# opos-selftest 1  2026-11-06T21:20:00Z  host=phone  uptime=1234s  opos=0123456789ab  read-only\n"
    "PASS  svc.opos               opos.service is active (running)\n"
    "WARN  clock.timezone         timezone is UTC, the default | the lock screen clock shows UTC\n"
    "SKIP  ota.updater            no /var/lib/opos-update\n"
    "SELFTEST: 1 PASS, 1 WARN, 1 SKIP, 0 FAIL\n")
REV = "0123456789abcdef0123456789abcdef01234567"


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed here; the text checks and the parser stand in")
class AcceptanceRun(unittest.TestCase):
    """deploy/duo-acceptance.ps1 run by pwsh against tests/fixtures/fake_ssh.py (no phone, no network)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="opos-acc-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = ROOT / "tests/fixtures/fake_ssh.py"
        if os.name == "nt":
            self.shim = self.tmp / "ssh.cmd"
            self.shim.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, self.fake), encoding="ascii")
        else:
            self.shim = self.tmp / "ssh"
            self.shim.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, self.fake), encoding="ascii")
            self.shim.chmod(0o755)
        self.sc = self.tmp / "scenario.json"
        self.out = self.tmp / "evidence"

    def scenario(self, selftest=(SELFTEST_OUT, 0), diag="== boot diag\n", devtools=True, viewport=None, ssh_fail=None, no_selftest=False):
        cmds = {"cat /opt/opos/REVISION": {"out": REV + "\n", "rc": 0},
                "test -r /var/log/opos-boot-diag.txt": {"out": "", "rc": 0 if diag is not None else 1},
                "cat /var/log/opos-boot-diag.txt": {"out": diag or "", "rc": 0}}
        if not no_selftest:
            cmds["/usr/sbin/opos-selftest"] = {"out": selftest[0], "rc": selftest[1]}
        self.sc.write_text(json.dumps({"commands": cmds, "devtools": devtools, "png": [1392, 900], "ssh_fail": ssh_fail, "page_url": "http://127.0.0.1:8765/",
                                       "viewport": viewport or {"w": 1392, "h": 900, "dpr": 2, "url": "http://127.0.0.1:8765/"}}), encoding="utf-8")

    def run_ps1(self, *args, host="phone.test"):
        env = dict(os.environ, OPOS_FAKE_SSH=str(self.sc))
        p = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(PS1), "-Host", host, "-SshPath", str(self.shim), "-OutDir", str(self.out), *args],
                           capture_output=True, text=True, timeout=180, env=env)
        runs = sorted(self.out.glob("duo-acceptance-*")) if self.out.exists() else []
        return p, (runs[-1] if runs else None)

    def calls(self):
        f = Path(str(self.sc) + ".log")
        return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []

    def test_a_good_run_leaves_a_complete_hashed_evidence_folder(self):
        self.scenario()
        p, run = self.run_ps1("-Key", str(self.fake))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        names = sorted(f.name for f in run.iterdir())
        self.assertEqual(names, ["SHA256SUMS", "opos-boot-diag.txt", "page.png", "report.md", "revision.txt", "selftest.txt", "viewport.json"])
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertIn("Result: **PASS** (", report)
        self.assertIn("- Image revision (/opt/opos/REVISION): " + REV, report)
        self.assertIn("| PASS | svc.opos | opos.service is active (running) |", report)
        self.assertIn("timezone is UTC, the default \\| the lock screen clock shows UTC", report, "a pipe in evidence must not break the table")
        self.assertIn("| PASS | pc.screenshot | page.png, 1392x900 px", report)
        self.assertIn("| PASS | pc.viewport |", report)
        self.assertIn("2026-11-06T21:20:00Z", report)
        for f in run.iterdir():
            if f.name not in ("report.md", "SHA256SUMS"):
                self.assertIn("| %s | %d |" % (f.name, f.stat().st_size), report)
                self.assertIn(hashlib.sha256(f.read_bytes()).hexdigest(), report)
        for ln in (run / "SHA256SUMS").read_text().splitlines():
            digest, _, name = ln.partition("  ")
            self.assertEqual(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest, name)
        self.assertIn("report.md", (run / "SHA256SUMS").read_text())
        png = (run / "page.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(png[16:20], "big"), 1392)
        self.assertEqual(int.from_bytes(png[20:24], "big"), 900)
        self.assertEqual((run / "selftest.txt").read_bytes().replace(b"\r\n", b"\n"), SELFTEST_OUT.encode())

    def test_it_asks_the_phone_only_read_only_things_with_key_only_ssh(self):
        self.scenario()
        p, run = self.run_ps1("-Key", str(self.fake))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        remote = [c[-1] for c in self.calls() if "-N" not in c]
        self.assertEqual(sorted(set(remote)), sorted(["/usr/sbin/opos-selftest", "cat /opt/opos/REVISION", "test -r /var/log/opos-boot-diag.txt", "cat /var/log/opos-boot-diag.txt"]))
        tunnels = [c for c in self.calls() if "-N" in c]
        self.assertEqual(len(tunnels), 1)
        spec = tunnels[0][tunnels[0].index("-L") + 1]
        self.assertRegex(spec, r"^127\.0\.0\.1:\d+:127\.0\.0\.1:9222$")
        for c in self.calls():
            self.assertIn("BatchMode=yes", c)
            self.assertIn("PasswordAuthentication=no", c)
            self.assertIn("droidian@phone.test", c)
            self.assertEqual(c[c.index("-i") + 1], str(self.fake), "-Key reaches ssh as -i")
            self.assertNotIn("scp", " ".join(c))

    def test_a_fail_line_fails_the_run_and_says_so_in_the_report(self):
        self.scenario(selftest=("# opos-selftest 1  2026-11-06T21:20:00Z  host=p  uptime=9s  opos=x  read-only\nFAIL  svc.opos               opos.service is failed (failed)\nSELFTEST: 0 PASS, 0 WARN, 0 SKIP, 1 FAIL\n", 1))
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertIn("Result: **FAIL**", report)
        self.assertIn("| FAIL | svc.opos |", report)

    def test_an_exit_status_that_disagrees_with_the_lines_fails(self):
        self.scenario(selftest=(SELFTEST_OUT, 1))
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("| FAIL | pc.selftest |", (run / "report.md").read_text(encoding="utf-8"))

    def test_ssh_that_cannot_log_in_exits_two_and_still_writes_a_report(self):
        self.scenario(ssh_fail="Permission denied (publickey).")
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertIn("| FAIL | pc.ssh | ssh to droidian@phone.test failed: Permission denied (publickey). |", report)
        self.assertEqual(len(self.calls()), 1, "it gives up after the first refusal")

    def test_text_from_the_phone_cannot_add_rows_or_markup_to_the_report(self):
        hostile = SELFTEST_OUT.replace("SKIP  ota.updater            no /var/lib/opos-update\n",
                                       "SKIP  ota.updater            x \x1b[31m<img src=x> y\n")
        self.scenario(selftest=(hostile, 0))
        p, run = self.run_ps1("-Key", str(self.fake))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("\x1b", report)
        self.assertNotIn("<img", report)
        self.assertIn("| SKIP | ota.updater | x [31m&lt;img src=x> y |", report)
        self.scenario(ssh_fail="Permission denied.\n| PASS | forged | row |\n## heading")
        p, run = self.run_ps1()
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("forged"), 1)
        self.assertIn("failed: Permission denied. \\| PASS \\| forged \\| row \\| ## heading |", report)

    def test_an_image_without_the_selftest_exits_two(self):
        self.scenario(no_selftest=True)
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("| FAIL | pc.selftest | /usr/sbin/opos-selftest is not on the phone", (run / "report.md").read_text(encoding="utf-8"))

    def test_no_devtools_port_skips_the_screenshot_unless_it_is_required(self):
        self.scenario(devtools=False)
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| SKIP | pc.screenshot | no DevTools answer", (run / "report.md").read_text(encoding="utf-8"))
        self.assertFalse((run / "page.png").exists())
        p, run = self.run_ps1("-RequireScreenshot")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_a_window_that_is_not_the_two_panel_size_warns(self):
        self.scenario(viewport={"w": 675, "h": 900, "dpr": 2, "url": "http://127.0.0.1:8765/"})
        p, run = self.run_ps1()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| WARN | pc.viewport | the page sees 675x900", (run / "report.md").read_text(encoding="utf-8"))

    def test_no_screenshot_and_no_boot_diag_are_skips(self):
        self.scenario(diag=None)
        p, run = self.run_ps1("-NoScreenshot")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        report = (run / "report.md").read_text(encoding="utf-8")
        self.assertIn("| SKIP | pc.screenshot | switched off", report)
        self.assertIn("| SKIP | pc.boot-diag |", report)
        self.assertEqual([c for c in self.calls() if "-N" in c], [])

    def test_a_host_that_looks_like_an_ssh_option_is_refused_before_anything_runs(self):
        self.scenario()
        p, run = self.run_ps1(host="-oProxyCommand=calc")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("not a plain host name", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
