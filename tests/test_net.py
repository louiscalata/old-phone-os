# SPDX-License-Identifier: GPL-3.0-or-later
"""Network status and Wi-Fi control (opos.net, opos.net_page) against fake nmcli/mmcli/rfkill/ip/bluetoothctl scripts.

The fakes are shell scripts on a private PATH, so the real runner, its timeouts and the way a password is handed
to nmcli (a private file, or opted-in terminal input, never a password argument) are exercised for real. What these cannot show: how the phone's own
NetworkManager, ModemManager and polkit behave; that needs the live recipe on the device.
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from opos import net, net_page
from src.opos import web

if sys.platform == "win32":
    raise unittest.SkipTest("the fake tools are shell scripts and the runner reads /proc")


class Running:
    """The real server on a free port, in a thread."""

    def __init__(self, phone=None):
        self.server = web.make_server(0, phone or web.Phone())
        self.port = self.server.server_address[1]
        self.token = self.server.RequestHandlerClass.token
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False


def request(port, method, path, body=None, headers=None):
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()


def fetch_token(port):
    return re.search(rb'<meta name="opos-token" content="([^"]+)">', request(port, "GET", "/")[2]).group(1).decode()


def post_action(port, action, token=None, **fields):
    body = json.dumps({"action": action, **fields}).encode()
    status, hdrs, raw = request(port, "POST", "/api", body, {"Content-Type": "application/json", "X-OPOS-Token": token or fetch_token(port)})
    return status, hdrs, json.loads(raw) if raw else None

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse 9"
WRONG = "nope nope nope"
UUID_HOME = "11111111-1111-4111-8111-111111111111"
UUID_CAFE = "22222222-2222-4222-8222-222222222222"
UUID_GSM = "33333333-3333-4333-8333-333333333333"
UUID_NEW = "99999999-0000-4000-8000-000000000001"

NMCLI = r"""#!/bin/sh
D='@D@'
echo "$@" >> "$D/calls.log"
PWFILE=
kept=
: > "$D/argv.new"
while [ $# -gt 0 ]; do
  if [ "$1" = "passwd-file" ]; then PWFILE=$2; shift 2; continue; fi
  printf '%s\n' "$1" >> "$D/argv.new"
  shift
done
set --
while IFS= read -r kept; do
  set -- "$@" "$kept"
done < "$D/argv.new"
rm -f "$D/argv.new"
ASK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ask|-a) ASK=1; shift;;
    -t|-p|-g) shift;;
    -f|-w|-c|--wait|--colors|--fields) shift 2;;
    *) break;;
  esac
done
[ -f "$D/nm_hang" ] && exec sleep 30
[ -f "$D/nm_down" ] && { echo "Error: NetworkManager is not running." >&2; exit 8; }
case "$1 $2" in
  "general status") cat "$D/general_status";;
  "general permissions") cat "$D/permissions";;
  "device status") cat "$D/device_status";;
  "device show") cat "$D/show_$3";;
  "connection show") if [ "$3" = uuid ]; then cat "$D/ssid_$4"; else
      if [ -f "$D/list_fails_after" ]; then
        n=$(cat "$D/list_count" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$D/list_count"
        if [ "$n" -gt "$(cat "$D/list_fails_after")" ]; then echo "Error: NetworkManager went away." >&2; exit 1; fi
      fi
      cat "$D/connections"
    fi;;
  "connection delete") echo "$4" >> "$D/deleted"; grep -v ":$4:" "$D/connections" > "$D/c.new"; mv "$D/c.new" "$D/connections"; echo "Connection successfully deleted.";;
  "connection add") printf '%s\n' "$*" > "$D/cmdline.log"; echo "Connection '$6' (@UNEW@) successfully added.";;
  "connection up")
    if [ -n "$PWFILE" ]; then
      tr '\0' ' ' < /proc/$$/cmdline >> "$D/cmdline.log"
      echo "$PWFILE" > "$D/pwfile"
      stat -c %a "$PWFILE" > "$D/pwmode" 2>/dev/null || true
      cp "$PWFILE" "$D/seen-key"
      [ -f "$D/join_denied" ] && { echo "Error: Failed to add/activate new connection: Not authorized to control networking." >&2; exit 1; }
      [ -f "$D/join_hang" ] && exec sleep 30
      pw=$(sed -n 's/.*psk://p' "$PWFILE" | head -n 1)
      if [ -f "$D/psk" ] && [ "$pw" != "$(cat "$D/psk")" ]; then
        if [ -f "$D/leave_profile" ]; then echo "$4:@UNEW@:wifi:" >> "$D/connections"; echo "802-11-wireless.ssid:$4" > "$D/ssid_@UNEW@"; fi
        echo "Error: Connection activation failed: (7) Secrets were required, but not provided. [$pw]" >&2; exit 4
      fi
      if [ -f "$D/activation_fails" ]; then
        if [ -f "$D/leave_profile" ]; then echo "$4:@UNEW@:wifi:" >> "$D/connections"; echo "802-11-wireless.ssid:$4" > "$D/ssid_@UNEW@"; fi
        cat "$D/activation_fails" >&2; exit 4
      fi
      if [ -f "$D/quiet_ok" ]; then echo "Connection activated"; exit 0; fi
      echo "Device 'wlan0' successfully activated with '$(cat "$D/activated_uuid" 2>/dev/null || echo @UNEW@)'."
    else
      echo "$4" >> "$D/up"; echo "Connection successfully activated."
    fi;;
  "radio wifi") echo "$3" > "$D/radio";;
  "device disconnect") echo "$3" > "$D/disc"; echo "Device successfully disconnected.";;
  "device wifi")
    case "$3" in
      list) cat "$D/wifi_list";;
      rescan) if [ -f "$D/rescan_err" ]; then cat "$D/rescan_err" >&2; exit 1; fi; touch "$D/rescanned";;
      connect)
        tr '\0' ' ' < /proc/$$/cmdline > "$D/cmdline.log"
        [ -f "$D/join_denied" ] && { echo "Error: Failed to add/activate new connection: Not authorized to control networking." >&2; exit 1; }
        [ -f "$D/join_hang" ] && exec sleep 30
        if [ -f "$D/psk" ]; then
          pw=""
          if [ "$ASK" = 1 ]; then
            if [ -f "$D/need_tty" ] && [ ! -t 0 ]; then echo "Error: Connection activation failed: (7) Secrets were required, but not provided." >&2; exit 4; fi
            [ -f "$D/need_tty" ] && printf 'Password (802-11-wireless-security.psk): '
            IFS= read -r pw
          fi
          if [ "$pw" != "$(cat "$D/psk")" ]; then
            if [ -f "$D/leave_profile" ]; then echo "$4:@UNEW@:wifi:" >> "$D/connections"; echo "802-11-wireless.ssid:$4" > "$D/ssid_@UNEW@"; fi
            echo "Error: Connection activation failed: (7) Secrets were required, but not provided. [$pw]" >&2; exit 4
          fi
        fi
        if [ -f "$D/activation_fails" ]; then  # the right password, then no address / the network went away
          if [ -f "$D/leave_profile" ]; then echo "$4:@UNEW@:wifi:" >> "$D/connections"; echo "802-11-wireless.ssid:$4" > "$D/ssid_@UNEW@"; fi
          cat "$D/activation_fails" >&2; exit 4
        fi
        if [ -f "$D/quiet_ok" ]; then echo "Connection activated"; exit 0; fi
        echo "Device 'wlan0' successfully activated with '$(cat "$D/activated_uuid" 2>/dev/null || echo @UNEW@)'."
        ;;
    esac;;
  *) echo "Error: unexpected: $*" >&2; exit 2;;
esac
"""
CAT_TOOL = r"""#!/bin/sh
D='@D@'
echo "@NAME@ $@" >> "$D/calls.log"
[ -f "$D/@NAME@_hang" ] && exec sleep 30
cat "$D/@NAME@_out"
exit $(cat "$D/@NAME@_rc" 2>/dev/null || echo 0)
"""

GENERAL = "running:connected:full:enabled:enabled:enabled:enabled\n"
PERMS = "".join(f"org.freedesktop.NetworkManager.{k}:{v}\n" for k, v in (
    ("checkpoint-rollback", "auth"), ("enable-disable-connectivity-check", "no"), ("enable-disable-network", "yes"),
    ("enable-disable-statistics", "no"), ("enable-disable-wifi", "yes"), ("enable-disable-wwan", "yes"), ("network-control", "yes"),
    ("reload", "auth"), ("settings.modify.global-dns", "auth"), ("settings.modify.hostname", "auth"), ("settings.modify.own", "yes"),
    ("settings.modify.system", "yes"), ("sleep-wake", "no"), ("wifi.scan", "yes"), ("wifi.share.open", "yes"), ("wifi.share.protected", "yes")))
DEVICES = ("wlan0:wifi:connected:HomeNet\np2p-dev-wlan0:wifi-p2p:disconnected:\nril_0:gsm:disconnected:\n"
           "rmnet_data0:ethernet:unmanaged:\nusb0:ethernet:unmanaged:\nlo:loopback:connected (externally):lo\n")
CONNS = (f"HomeNet:{UUID_HOME}:wifi:wlan0\nCafe\\: Free WiFi:{UUID_CAFE}:wifi:\nMobile data:{UUID_GSM}:gsm:\n"
         "lo:44444444-4444-4444-8444-444444444444:loopback:lo\n")
WIFI_LIST = ("yes:HomeNet:78:WPA2\nno:HomeNet:55:WPA2\nno:Cafe\\: Free WiFi:62:--\nno:Neighbor 5G:41:WPA2 WPA3\n"
             "no:Office:35:WPA2 802.1X\nno::28:WPA2\nno:OldRouter:22:WEP\n")
# `nmcli device show` as nmcli prints it: padded columns, not terse.
SHOW_WLAN0 = ("IP4.ADDRESS[1]:                         198.51.100.23/24\n"
              "IP4.GATEWAY:                            198.51.100.1\nIP6.ADDRESS[1]:                         fe80::aaaa:bbbb:cccc:dddd/64\n")
RFKILL = ("0: phy0: Wireless LAN\n\tSoft blocked: no\n\tHard blocked: no\n"
          "1: hci0: Bluetooth\n\tSoft blocked: no\n\tHard blocked: no\n")
IP_BR = ("lo               UNKNOWN        127.0.0.1/8 ::1/128 \nwlan0            UP             198.51.100.23/24 fe80::aaaa:bbbb:cccc:dddd/64 \n"
         "usb0             UNKNOWN        203.0.113.1/24 fe80::1/64 \nrmnet_data0      DOWN           \n")
BTCTL = "Controller 5C:BA:37:AA:BB:CC (public)\n\tName: duo\n\tAlias: duo\n\tPowered: yes\n\tDiscoverable: no\n"
MMCLI = """modem.dbus-path                                  : /org/freedesktop/ModemManager1/Modem/0
modem.generic.device-identifier                  : 0123456789abcdef
modem.generic.manufacturer                       : QUALCOMM INCORPORATED
modem.generic.state                              : registered
modem.generic.state-failed-reason                : --
modem.generic.power-state                        : on
modem.generic.access-technologies.length         : 1
modem.generic.access-technologies.value[1]       : lte
modem.generic.signal-quality.value               : 75
modem.generic.signal-quality.recent              : yes
modem.generic.sim                                : /org/freedesktop/ModemManager1/SIM/0
modem.generic.unlock-required                    : --
modem.3gpp.imei                                  : 356938035643809
modem.3gpp.registration-state                    : home
modem.3gpp.operator-name                         : Example Mobile
"""


class Fake:
    """A private directory of fake tools and canned outputs, plus a sysfs root for read_radios."""

    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix="opos-net-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.tools = self.dir / "tools"  # only what the fake scripts call, so a real nmcli or ip can never be reached
        self.tools.mkdir()
        for tool in ("cat", "tr", "sleep", "grep", "mv", "touch", "env", "sort", "sed", "head", "timeout", "rm", "cp", "stat"):
            if shutil.which(tool):
                (self.tools / tool).symlink_to(shutil.which(tool))
        self.root = self.dir / "root"
        for p in ("sys/class/net/wlan0", "sys/class/net/usb0", "sys/class/rfkill/rfkill0", "sys/class/rfkill/rfkill1"):
            (self.root / p).mkdir(parents=True)
        (self.root / "sys/class/rfkill/rfkill0/type").write_text("wlan\n")
        (self.root / "sys/class/rfkill/rfkill1/type").write_text("bluetooth\n")
        for name, body in (("nmcli", NMCLI),):
            self._script(name, body)
        for name, out in (("mmcli", MMCLI), ("rfkill", RFKILL), ("ip", IP_BR), ("bluetoothctl", BTCTL)):
            self._script(name, CAT_TOOL.replace("@NAME@", name))
            self.put(f"{name}_out", out)
        for name, out in (("general_status", GENERAL), ("permissions", PERMS), ("device_status", DEVICES), ("connections", CONNS),
                          ("wifi_list", WIFI_LIST), ("show_wlan0", SHOW_WLAN0), (f"ssid_{UUID_HOME}", "802-11-wireless.ssid:HomeNet\n"),
                          (f"ssid_{UUID_CAFE}", "802-11-wireless.ssid:Cafe\\: Free WiFi\n"), ("calls.log", ""), ("deleted", "")):
            self.put(name, out)

    def _script(self, name, body):
        p = self.bin / name
        p.write_text(body.replace("@D@", str(self.dir)).replace("@UNEW@", UUID_NEW), newline="\n")
        p.chmod(0o755)

    def put(self, name, text=""):
        (self.dir / name).write_text(text, newline="\n")

    def rm(self, name):
        (self.dir / name).unlink(missing_ok=True)

    def read(self, name):
        p = self.dir / name
        return p.read_text() if p.exists() else ""

    def calls(self):
        return self.read("calls.log")

    def net(self, path=None, **kw):
        env = kw.pop("env", None)
        env = {"OPOS_NETWORK": "1"} if env is None else env if env is os.environ else {"OPOS_NETWORK": "1", **env}
        runner = net.make_runner(path or f"{self.bin}:{self.tools}")
        return net.Net(runner=runner, root=self.root, env=env, sleep=lambda s: None, wall=lambda: 1.8e9, **kw)


class Terse(unittest.TestCase):
    def test_split_terse(self):
        self.assertEqual(net.split_terse("yes:HomeNet:78:WPA2"), ["yes", "HomeNet", "78", "WPA2"])
        self.assertEqual(net.split_terse("no:Cafe\\: Free WiFi:62:--"), ["no", "Cafe: Free WiFi", "62", "--"])
        self.assertEqual(net.split_terse("a\\\\b:c"), ["a\\b", "c"])
        self.assertEqual(net.split_terse("no::28:WPA2"), ["no", "", "28", "WPA2"])
        self.assertEqual(net.split_terse("x:\n"), ["x", ""])
        self.assertEqual(net.split_terse("AA\\:BB\\:CC:ssid"), ["AA:BB:CC", "ssid"])

    def test_validation(self):
        self.assertIsNone(net.check_ssid("HomeNet"))
        self.assertIsNone(net.check_ssid("Caf\u00e9 5G"))
        for bad in ("", None, 5, "x" * 33, "a\nb", "-evil", "\u202eRLO", "a\x00b", "bad\udcff", "\ud800"):
            self.assertIsNotNone(net.check_ssid(bad), repr(bad))
        self.assertIsNone(net.check_password("12345678"))
        self.assertIsNone(net.check_password("p" * 63))
        self.assertIsNone(net.check_password("ab" * 32))
        for bad in ("", None, "short", "p" * 64, "x" * 100, "pass\nword!", "pass word\x00", "p\u00e4ssword1", "caf\u00e9caf\u00e9"):
            self.assertIsNotNone(net.check_password(bad), repr(bad))

    def test_explain_is_plain_and_never_holds_the_secret(self):
        cases = {
            "Error: Connection activation failed: (7) Secrets were required, but not provided.": "password was not accepted",
            "Error: No network with SSID 'x' found.": "not in range",
            "Error: Failed to add/activate new connection: Insufficient privileges": "does not allow",
            "Error: 802-11-wireless-security.psk: property is invalid": "not valid",
            "Error: Connection activation failed: IP configuration could not be reserved (no available address, timeout, etc.)": "no address",
            "Error: NetworkManager is not running.": "not running",
            "Error: Wi-Fi is disabled": "switched off",
            "Error: Connection activation failed: No suitable device found for this connection (device wlan0 not available because profile is not compatible with device (mismatching ssid)).": "not in range",
            "Error: Could not create NMClient object: Could not connect: No such file or directory.": "not reachable",
        }
        for err, want in cases.items():
            self.assertIn(want, net.explain(net.Run(4, "", err)))
        self.assertIn("did not answer in time", net.explain(net.Run(124, timed_out=True)))
        self.assertIn("not installed", net.explain(net.Run(127, missing=True)))
        leaked = net.explain(net.Run(1, "", "Error: weird failure for key hunter22hunter22 here"), "hunter22hunter22")
        self.assertNotIn("hunter22hunter22", leaked)
        self.assertIn("refused", leaked)
        self.assertIn("did not say why", net.explain(net.Run(1)))


class Runner(unittest.TestCase):
    def test_missing_tool_and_empty_path(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = net.run(["nmcli", "general"], 2, path=d)
        self.assertTrue(r.missing)
        self.assertEqual(r.rc, 127)

    def test_timeout_kills_the_tool(self):
        f = Fake(self)
        f.put("nm_hang")
        t = time.monotonic()
        r = net.make_runner(f"{f.bin}:{f.tools}")(["nmcli", "general", "status"], 0.4)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - t, 5)

    def test_no_shell_and_clean_environment(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        p = d / "envdump"
        p.write_text("#!/bin/sh\nenv | sort\n")
        p.chmod(0o755)
        with mock.patch.dict(os.environ, {"OPOS_SECRET_TEST": "x", "PATH": "/usr/bin:/bin"}):
            r = net.run(["envdump"], 5, path=f"{d}:/usr/bin:/bin")
        self.assertIn("LC_ALL=C.UTF-8", r.out)
        self.assertNotIn("OPOS_SECRET_TEST", r.out)
        r = net.run(["echo", "a;touch /tmp/opos-should-not-exist"], 5)
        self.assertIn(";touch", r.out)


class Status(unittest.TestCase):
    def test_everything_connected(self):
        f = Fake(self)
        s = f.net().status(True)
        self.assertTrue(s["ok"])
        w = s["wifi"]
        self.assertEqual((w["state"], w["ssid"], w["signal"], w["ip"], w["device"], w["security"]), ("connected", "HomeNet", 78, "198.51.100.23", "wlan0", "WPA2"))
        self.assertIn("connected to HomeNet", w["summary"])
        self.assertIn("198.51.100.23", w["summary"])
        self.assertEqual((s["bluetooth"]["state"], s["bluetooth"]["enabled"]), ("on", True))
        self.assertEqual((s["modem"]["state"], s["modem"]["operator"], s["modem"]["tech"], s["modem"]["signal"]), ("registered", "Example Mobile", "lte", 75))
        self.assertIn("Example Mobile", s["modem"]["summary"])
        self.assertEqual((s["sim"]["present"], s["sim"]["locked"]), (True, False))
        self.assertEqual(s["data"]["state"], "off")
        self.assertIn("off while Wi-Fi is connected", s["data"]["summary"])
        self.assertEqual(s["airplane"]["on"], False)
        self.assertEqual((s["usb"]["present"], s["usb"]["device"], s["usb"]["ip"], s["usb"]["state"]), (True, "usb0", "203.0.113.1", "up"))
        self.assertEqual((s["control"]["enabled"], s["control"]["allowed"]), (True, True))
        self.assertEqual(s["control"]["ops"]["join"], "yes")
        self.assertEqual(s["tools_missing"], [])
        self.assertEqual(s["notes"], [])
        self.assertIn("still simulated", s["simulated"])

    def test_status_carries_no_identifiers(self):
        f = Fake(self)
        blob = json.dumps(f.net().status(True))
        for secret in ("356938035643809", "5C:BA:37", "0123456789abcdef", "fe80::", "198.51.100.1"):
            self.assertNotIn(secret, blob)

    def test_wifi_off_blocked_and_disconnected(self):
        f = Fake(self)
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:enabled\n")
        f.put("device_status", DEVICES.replace("connected:HomeNet", "unavailable:"))
        self.assertEqual(f.net().status(True)["wifi"]["state"], "off")
        f.put("general_status", "running:connected:none:disabled:enabled:enabled:enabled\n")
        self.assertEqual(f.net().status(True)["wifi"]["state"], "blocked")
        f.put("general_status", "running:disconnected:none:enabled:enabled:enabled:enabled\n")
        f.put("device_status", DEVICES.replace("connected:HomeNet", "disconnected:"))
        w = f.net().status(True)["wifi"]
        self.assertEqual((w["state"], w["ssid"]), ("disconnected", None))
        self.assertEqual(w["summary"], "Wi-Fi: on, not connected")
        f.put("device_status", DEVICES.replace("connected:HomeNet", "connecting (getting IP configuration):HomeNet"))
        self.assertEqual(f.net().status(True)["wifi"]["state"], "connecting")

    def test_portal_and_limited_internet_are_not_called_working(self):
        f = Fake(self)
        f.put("general_status", "running:connected (site only):limited:enabled:enabled:enabled:enabled\n")
        w = f.net().status(True)["wifi"]
        self.assertIs(w["internet"], False)
        self.assertIn("no internet", w["summary"])
        f.put("general_status", "running:connected:portal:enabled:enabled:enabled:enabled\n")
        self.assertIn("sign-in page", f.net().status(True)["wifi"]["summary"])
        f.put("general_status", "running:connected:unknown:enabled:enabled:enabled:enabled\n")
        w = f.net().status(True)["wifi"]
        self.assertIsNone(w["internet"])
        self.assertNotIn("internet", w["summary"])

    def test_airplane_mode(self):
        f = Fake(self)
        f.put("rfkill_out", RFKILL.replace("Soft blocked: no", "Soft blocked: yes"))
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:disabled\n")
        s = f.net().status(True)
        self.assertTrue(s["airplane"]["on"])
        self.assertEqual(s["bluetooth"]["state"], "off")
        f.put("rfkill_out", RFKILL.replace("Soft blocked: no", "Soft blocked: yes", 1))
        self.assertEqual(f.net().status(True)["airplane"]["state"], "partly")
        f.put("rfkill_out", RFKILL.replace("Hard blocked: no", "Hard blocked: yes"))
        self.assertEqual(f.net().status(True)["bluetooth"]["state"], "blocked")

    def test_rfkill_missing_and_bluetooth_on_is_not_all_radios_blocked(self):
        f = Fake(self)
        f.put("rfkill_rc", "1")  # rfkill fails: nothing tells whether Bluetooth is blocked, except BlueZ itself
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:disabled\n")  # NetworkManager: Wi-Fi and mobile data off
        s = f.net().status(True)
        self.assertEqual((s["bluetooth"]["state"], s["bluetooth"]["enabled"]), ("on", True))
        self.assertNotEqual(s["airplane"]["state"], "on")
        self.assertIsNot(s["airplane"]["on"], True)
        self.assertNotIn("all radios are blocked", s["airplane"]["summary"])
        self.assertEqual(s["airplane"]["state"], "partly")

    def test_rfkill_missing_and_bluetooth_off_is_all_radios_blocked(self):
        f = Fake(self)
        f.put("rfkill_rc", "1")
        f.put("bluetoothctl_out", BTCTL.replace("Powered: yes", "Powered: no"))
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:disabled\n")
        s = f.net().status(True)
        self.assertEqual(s["bluetooth"]["state"], "off")
        self.assertIs(s["airplane"]["on"], True)
        self.assertIn("all radios are blocked", s["airplane"]["summary"])

    def test_rfkill_missing_and_bluetooth_not_known_keeps_the_answer_unknown(self):
        f = Fake(self)
        f.put("rfkill_rc", "1")
        f.put("bluetoothctl_out", "")  # BlueZ answers but names no controller: the radio is there, its state is not established
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:disabled\n")
        s = f.net().status(True)
        self.assertEqual(s["bluetooth"]["enabled"], None)
        self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "unknown"))
        self.assertNotIn("all radios are blocked", s["airplane"]["summary"])
        self.assertIn("Bluetooth", s["airplane"]["summary"])

    def test_an_unknown_radio_does_not_hide_a_radio_that_is_known_to_work(self):
        f = Fake(self)
        f.put("rfkill_rc", "1")
        f.put("bluetoothctl_out", "")
        f.put("general_status", "running:connected:none:enabled:enabled:enabled:disabled\n")  # Wi-Fi is on
        s = f.net().status(True)
        self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "partly"))

    def test_a_wifi_radio_nobody_reported_on_keeps_airplane_unknown(self):
        f = Fake(self)
        f.put("nm_down")  # NetworkManager says nothing about Wi-Fi or mobile data
        f.put("rfkill_out", "1: hci0: Bluetooth\n\tSoft blocked: yes\n\tHard blocked: no\n")  # rfkill lists only Bluetooth, and it is blocked
        (f.bin / "mmcli").unlink()  # and this phone has no modem to be read: only the Wi-Fi radio is in doubt
        s = f.net().status(True)
        self.assertEqual(s["bluetooth"]["state"], "off")
        self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "unknown"))
        self.assertIn("Wi-Fi", s["airplane"]["summary"])
        self.assertNotIn("all radios are blocked", s["airplane"]["summary"])

    def modem_case(self, mmcli_state=None, mmcli_rc=None, mmcli_out=None, wwan_iface=True, rfkill=None):
        """NetworkManager down, Wi-Fi and Bluetooth blocked in rfkill, which lists no Wireless WAN row (the normal Droidian
        case); the phone has a modem (an rmnet interface and/or ModemManager's answer), but nothing has said whether it is blocked."""
        f = Fake(self)
        f.put("nm_down")
        f.put("rfkill_out", rfkill if rfkill is not None else RFKILL.replace("Soft blocked: no", "Soft blocked: yes"))
        if wwan_iface:
            (f.root / "sys/class/net/rmnet_data0").mkdir()
        if mmcli_state:
            f.put("mmcli_out", MMCLI.replace("registered", mmcli_state, 1))
        if mmcli_rc is not None:
            f.put("mmcli_rc", str(mmcli_rc))
            f.put("mmcli_out", mmcli_out or "")
        return f.net().status(True)

    def test_a_modem_nobody_reported_on_keeps_airplane_unknown(self):
        for how, kw in {"mmcli fails": dict(mmcli_rc=1), "ModemManager sees no modem": dict(mmcli_rc=1, mmcli_out="error: couldn't find modem")}.items():
            with self.subTest(how):
                s = self.modem_case(**kw)
                self.assertEqual(s["bluetooth"]["state"], "off")
                self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "unknown"), s["airplane"])
                self.assertIn("mobile data", s["airplane"]["summary"])
                self.assertNotIn("all radios are blocked", s["airplane"]["summary"])

    def test_a_modem_that_is_on_means_airplane_mode_is_not_all_radios_blocked(self):
        for state in ("registered", "connected", "searching", "enabled"):
            with self.subTest(state):
                s = self.modem_case(mmcli_state=state, wwan_iface=False)  # ModemManager alone shows the modem
                self.assertEqual(s["modem"]["state"], state)
                self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "partly"), s["airplane"])
                self.assertNotIn("all radios are blocked", s["airplane"]["summary"])

    def test_a_modem_whose_state_says_nothing_about_the_radio_keeps_airplane_unknown(self):
        for state in ("disabled", "locked", "failed", "initializing", "unknown"):
            with self.subTest(state):
                s = self.modem_case(mmcli_state=state)
                self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (None, "unknown"), s["airplane"])
                self.assertIn("mobile data", s["airplane"]["summary"])

    def test_the_same_case_with_the_modem_read_as_on_is_the_status_the_page_shows_for_the_modem(self):
        s = self.modem_case()  # the default ModemManager answer: registered on Example Mobile
        self.assertIn("registered", s["modem"]["summary"])
        self.assertNotIn("all radios are blocked", s["airplane"]["summary"], "the page must not say both")

    def test_rfkill_listing_the_modem_decides_whatever_modemmanager_says(self):
        both = RFKILL.replace("Soft blocked: no", "Soft blocked: yes") + "2: wwan0: Wireless WAN\n\tSoft blocked: yes\n\tHard blocked: no\n"
        s = self.modem_case(rfkill=both)  # registered per mmcli, blocked per rfkill: rfkill is the switch
        self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (True, "on"))

    def test_a_phone_without_a_modem_has_nothing_unknown_about_one(self):
        f = Fake(self)
        f.put("nm_down")
        f.put("rfkill_out", RFKILL.replace("Soft blocked: no", "Soft blocked: yes"))
        (f.bin / "mmcli").unlink()  # no ModemManager tool, and no wwan interface in the fixture: there is no modem to be unsure about
        s = f.net().status(True)
        self.assertIn("mmcli", s["tools_missing"])
        self.assertEqual((s["airplane"]["on"], s["airplane"]["state"]), (True, "on"))

    def test_unknown_radios_are_listed_in_plain_words(self):
        f = Fake(self)
        f.put("nm_down")
        f.put("rfkill_rc", "1")
        f.put("bluetoothctl_out", "")
        f.put("mmcli_rc", "1")
        (f.root / "sys/class/net/rmnet_data0").mkdir()
        s = f.net().status(True)["airplane"]
        self.assertIn("the state of Wi-Fi, mobile data and Bluetooth could not be read", s["summary"])

    def test_bluetooth_cases(self):
        f = Fake(self)
        f.put("bluetoothctl_out", BTCTL.replace("Powered: yes", "Powered: no"))
        self.assertEqual(f.net().status(True)["bluetooth"]["state"], "off")
        f.put("bluetoothctl_out", "No default controller available\n")
        f.put("bluetoothctl_rc", "1")
        b = f.net().status(True)["bluetooth"]
        self.assertEqual(b["state"], "no controller")
        self.assertIn("sees no controller", b["summary"])
        (f.bin / "bluetoothctl").unlink()
        b = f.net().status(True)["bluetooth"]  # rfkill shows the radio but BlueZ is not asked: do not claim it has no controller
        self.assertEqual(b["state"], "unknown")
        self.assertIn("not installed", b["summary"])
        f.put("rfkill_out", "")
        for p in ("sys/class/rfkill/rfkill1", "sys/class/rfkill/rfkill0"):
            shutil.rmtree(f.root / p)
        self.assertEqual(f.net().status(True)["bluetooth"]["state"], "unavailable")

    def test_vendor_bt_power_does_not_turn_hci_off(self):
        f = Fake(self)
        f.put("rfkill_out", RFKILL + "2: bt_power: Bluetooth\n\tSoft blocked: yes\n\tHard blocked: no\n")
        s = f.net().status(True)
        self.assertEqual(s["bluetooth"]["state"], "on")
        self.assertNotIn("blocked", s["airplane"]["summary"])
        self.assertNotIn("bt_power", f.calls())

    def test_modem_and_sim_cases(self):
        f = Fake(self)
        f.put("mmcli_out", "error: couldn't find modem\n")
        f.put("mmcli_rc", "1")
        s = f.net().status(True)
        self.assertEqual((s["modem"]["state"], s["modem"]["present"]), ("absent", False))
        self.assertIn("unknown", s["sim"]["summary"])
        f.put("mmcli_out", MMCLI.replace("registered", "failed", 1).replace("state-failed-reason                : --", "state-failed-reason                : sim-missing")
              .replace("sim                                : /org/freedesktop/ModemManager1/SIM/0", "sim                                : --"))
        f.put("mmcli_rc", "0")
        s = f.net().status(True)
        self.assertEqual(s["sim"]["state"], "absent")
        self.assertIn("failed (sim-missing)", s["modem"]["summary"])
        f.put("mmcli_out", MMCLI.replace("unlock-required                    : --", "unlock-required                    : sim-pin").replace(": registered", ": locked"))
        s = f.net().status(True)
        self.assertEqual((s["sim"]["state"], s["sim"]["locked"]), ("locked", True))
        self.assertIn("needs a PIN", s["sim"]["summary"])
        f.put("mmcli_out", MMCLI.replace(": registered", ": searching"))
        self.assertEqual(f.net().status(True)["modem"]["summary"], "Modem: searching for a network")
        f.put("mmcli_out", MMCLI.replace(": registered", ": initializing").replace("sim                                : /org/freedesktop/ModemManager1/SIM/0", "sim                                : --"))
        self.assertIn("not known yet", f.net().status(True)["sim"]["summary"])

    def test_mobile_data(self):
        f = Fake(self)
        f.put("connections", CONNS.replace(f"{UUID_GSM}:gsm:", f"{UUID_GSM}:gsm:ril_0"))
        self.assertEqual(f.net().status(True)["data"]["state"], "on")
        f.put("connections", "\n".join(l for l in CONNS.splitlines() if ":gsm:" not in l) + "\n")
        self.assertEqual(f.net().status(True)["data"]["state"], "not set up")
        f.put("device_status", DEVICES.replace("connected:HomeNet", "disconnected:"))
        f.put("connections", CONNS)
        self.assertEqual(f.net().status(True)["data"]["summary"], "Mobile data: off")

    def test_mobile_data_is_off_when_nmcli_prints_dashes_for_no_device(self):
        f = Fake(self)  # some nmcli versions print "--" in the DEVICE column of an inactive profile
        f.put("device_status", DEVICES.replace("connected:HomeNet", "disconnected:"))
        f.put("connections", CONNS.replace(f"{UUID_GSM}:gsm:", f"{UUID_GSM}:gsm:--"))
        d = f.net().status(True)["data"]
        self.assertEqual((d["on"], d["state"]), (False, "off"))
        f.put("connections", CONNS.replace(f"{UUID_GSM}:gsm:", f"{UUID_GSM}:gsm:ril_0"))
        self.assertTrue(f.net().status(True)["data"]["on"])

    def test_usb_network(self):
        f = Fake(self)
        f.put("ip_out", "lo UNKNOWN 127.0.0.1/8\nwlan0 UP 198.51.100.23/24\nusb0 DOWN\n")
        u = f.net().status(True)["usb"]
        self.assertEqual((u["present"], u["ip"], u["state"]), (True, None, "no address"))
        f.put("ip_out", "lo UNKNOWN 127.0.0.1/8\nwlan0 UP 198.51.100.23/24\n")
        self.assertEqual(f.net().status(True)["usb"]["state"], "absent")
        (f.bin / "ip").unlink()
        u = f.net().status(True)["usb"]
        self.assertTrue(u["present"])  # the sysfs listing still shows usb0
        self.assertIsNone(u["ip"])

    def test_networkmanager_down_or_missing_reads_unavailable_not_an_error(self):
        f = Fake(self)
        f.put("nm_down")
        s = f.net().status(True)
        self.assertEqual(s["wifi"]["state"], "unavailable")
        self.assertIn("NetworkManager", s["wifi"]["summary"])
        self.assertEqual(s["control"]["allowed"], False)
        self.assertIn("not available", s["control"]["reason"])
        self.assertEqual(s["data"]["state"], "unknown")
        self.assertEqual(s["wifi"]["ip"], "198.51.100.23")  # ip still answers
        # every tool missing
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)
        s = f.net(path=str(empty)).status(True)
        self.assertTrue(s["ok"])
        self.assertEqual(sorted(s["tools_missing"]), ["bluetoothctl", "ip", "mmcli", "nmcli", "rfkill"])
        self.assertIn("not installed", s["wifi"]["summary"])
        self.assertIn("not installed", s["modem"]["summary"])
        self.assertEqual(s["airplane"]["state"], "unknown")
        self.assertIn("still simulated", s["simulated"])

    def test_timeouts_are_bounded_by_one_budget(self):
        f = Fake(self)
        for t in ("nm", "mmcli", "rfkill", "ip", "bluetoothctl"):
            f.put(f"{t}_hang")
        n = f.net()
        t = time.monotonic()
        with mock.patch.object(net, "BUDGET_S", 1.0):
            s = n.status(True)
        self.assertLess(time.monotonic() - t, 6)
        self.assertTrue(s["ok"])
        self.assertEqual(s["wifi"]["state"], "unavailable")
        self.assertIn("did not answer in time", s["wifi"]["summary"])

    def test_a_runner_that_raises_still_reads_unavailable(self):
        def boom(argv, timeout=1, stdin=None):
            raise RuntimeError("tool exploded")
        s = net.Net(runner=boom, root="/nonexistent", env={}).status(True)
        self.assertTrue(s["ok"])
        self.assertEqual(s["wifi"]["state"], "unavailable")

    def test_clock_reports_explicit_sync_state_only_after_full_connectivity(self):
        f = Fake(self)
        tool = f.bin / "timedatectl"
        tool.write_text("#!/bin/sh\necho NTPSynchronized=no\n", encoding="utf-8")
        tool.chmod(0o755)
        self.assertEqual(f.net().status(True)["clock"],
                         {"state": "unsynchronized", "summary": "Clock: not synced"})
        tool.write_text("#!/bin/sh\necho NTPSynchronized=yes\n", encoding="utf-8")
        self.assertEqual(f.net().status(True)["clock"]["summary"], "Clock: set from the network")
        f.put("general_status", "running:connected:none:enabled:enabled:enabled:enabled\n")
        self.assertEqual(f.net().status(True)["clock"]["summary"], "Clock: no network time yet")

    def test_clock_query_failure_or_unknown_reply_does_not_claim_syncing(self):
        for reply in (net.Run(1, out="NTPSynchronized=yes"), net.Run(0),
                      net.Run(0, out="unexpected response"),
                      net.Run(0, out="NTPSynchronized=maybe"),
                      net.Run(0, out="NTPSynchronized=yes\nNTPSynchronized=no")):
            with self.subTest(reply=reply):
                n = net.Net(runner=lambda *args, result=reply: result,
                            root="/nonexistent", env={})
                clock = n._net_clock(None, {"connectivity": "full"})
                self.assertEqual(clock["state"], "unknown")
                self.assertIn("could not be checked", clock["summary"])

        def unavailable(*args):
            raise OSError("fixture tool failure")
        n = net.Net(runner=unavailable, root="/nonexistent", env={})
        self.assertEqual(n._net_clock(None, {"connectivity": "full"})["state"], "unknown")

    def test_clock_note_and_cache(self):
        f = Fake(self)
        n = f.net()
        n._wall = lambda: 0.0
        self.assertIn("clock is not set", " ".join(n.status(True)["notes"]))
        before = f.calls()
        n.status()
        self.assertEqual(f.calls(), before)  # cached for a few seconds
        n.status(True)
        self.assertNotEqual(f.calls(), before)

    def test_control_states(self):
        f = Fake(self)
        auth = PERMS.replace("settings.modify.system:yes", "settings.modify.system:auth")
        f.put("permissions", auth)
        c = f.net().status(True)["control"]
        self.assertEqual((c["allowed"], c["ops"]["join"], c["ops"]["forget"]), (False, "auth", "auth"))
        self.assertIn("administrator", c["reason"])
        self.assertEqual(c["ops"]["scan"], "yes")
        f.put("permissions", PERMS.replace("network-control:yes", "network-control:no"))
        c = f.net().status(True)["control"]
        self.assertEqual(c["ops"]["join"], "no")
        self.assertIn("does not let OPOS", c["reason"])
        f.put("permissions", PERMS.replace("enable-disable-wifi:yes", "enable-disable-wifi:no"))
        self.assertFalse(f.net().status(True)["control"]["allowed"])
        c = f.net(env={"OPOS_NETWORK": "0"}).status(True)["control"]
        self.assertEqual((c["enabled"], c["allowed"]), (False, False))
        self.assertIn("switched off", c["reason"])
        f.put("permissions", "")
        self.assertEqual(f.net().status(True)["control"]["ops"]["join"], "unknown")


class Scan(unittest.TestCase):
    def test_list_dedupes_and_sorts_and_flags(self):
        f = Fake(self)
        r = f.net().scan()
        self.assertTrue(r["ok"])
        self.assertTrue((f.dir / "rescanned").exists())
        names = [n["ssid"] for n in r["networks"]]
        self.assertEqual(names, ["HomeNet", "Cafe: Free WiFi", "Neighbor 5G", "Office", "OldRouter"])
        by = {n["ssid"]: n for n in r["networks"]}
        self.assertEqual(by["HomeNet"]["signal"], 78)
        self.assertTrue(by["HomeNet"]["active"] and by["HomeNet"]["known"])
        self.assertEqual(by["HomeNet"]["label"], "connected, signal 78%")
        self.assertEqual((by["Cafe: Free WiFi"]["secure"], by["Cafe: Free WiFi"]["known"]), (False, True))
        self.assertEqual(by["Cafe: Free WiFi"]["label"], "saved, signal 62%")
        self.assertEqual((by["Neighbor 5G"]["known"], by["Neighbor 5G"]["supported"], by["Neighbor 5G"]["label"]), (False, True, "needs a password, signal 41%"))
        self.assertFalse(by["Office"]["supported"])
        self.assertIn("company or school", by["Office"]["label"])
        self.assertFalse(by["OldRouter"]["supported"])
        self.assertIn("WEP", by["OldRouter"]["label"])
        self.assertEqual(r["hidden"], 1)
        self.assertEqual(r["message"], "5 networks found.")

    def test_saved_networks_out_of_range_are_listed_for_forgetting(self):
        f = Fake(self)
        f.put("wifi_list", "no:Neighbor 5G:41:WPA2\n")
        r = f.net().scan(False)
        self.assertEqual(r["saved"], ["Cafe: Free WiFi", "HomeNet"])
        self.assertFalse((f.dir / "rescanned").exists())

    def test_empty_scan(self):
        f = Fake(self)
        f.put("wifi_list", "")
        r = f.net().scan()
        self.assertTrue(r["ok"])
        self.assertEqual(r["networks"], [])
        self.assertEqual(r["message"], "0 networks found.")

    def test_throttled_rescan_still_lists(self):
        f = Fake(self)
        f.put("rescan_err", "Error: Scanning not allowed immediately following previous scan.\n")
        r = f.net().scan()
        self.assertTrue(r["ok"])
        self.assertIn("scan just ran", r["message"])
        self.assertEqual(len(r["networks"]), 5)

    def test_rescan_refused_by_polkit(self):
        f = Fake(self)
        f.put("permissions", PERMS.replace("wifi.scan:yes", "wifi.scan:no"))
        r = f.net().scan()
        self.assertFalse(r["ok"])
        self.assertIn("does not let OPOS", r["error"])
        f.put("permissions", PERMS)
        f.put("rescan_err", "Error: Not authorized to request a Wi-Fi scan\n")
        self.assertIn("does not allow", f.net().scan()["error"])

    def test_without_control_the_scan_only_lists(self):
        f = Fake(self)
        r = f.net(env={"OPOS_NETWORK": ""}).scan()
        self.assertTrue(r["ok"])
        self.assertIn("switched off on this install", r["message"])
        self.assertFalse((f.dir / "rescanned").exists())

    def test_wifi_off_or_nm_down(self):
        f = Fake(self)
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:enabled\n")
        self.assertIn("switched off", f.net().scan()["error"])
        f.put("nm_down")
        self.assertIn("not available", f.net().scan()["error"])


class Join(unittest.TestCase):
    def setUp(self):
        self.f = Fake(self)
        self.f.put("psk", PW)

    def test_wpa_join_uses_a_private_password_file_not_arguments(self):
        r = self.f.net().join("Neighbor 5G", PW)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"], "Connected to Neighbor 5G. The password is kept by the phone's network service, not by OPOS.")
        self.assertNotIn(PW, json.dumps(r))
        self.assertNotIn(PW, self.f.calls())
        self.assertNotIn(PW, self.f.read("cmdline.log"))
        cmd = self.f.read("cmdline.log")
        self.assertIn("passwd-file", cmd)
        self.assertNotIn("--ask", cmd)
        self.assertNotIn(PW, cmd)
        self.assertNotIn("password", cmd)
        self.assertEqual(self.f.read("pwmode").strip(), "600")
        self.assertFalse(os.path.exists(self.f.read("pwfile").strip()))
        self.assertIn("802-11-wireless-security.psk:" + PW, self.f.read("seen-key"))
        self.assertEqual(self.f.read("deleted"), "")

    def test_wrong_password_says_why_and_leaves_nothing(self):
        self.f.put("leave_profile")
        r = self.f.net().join("Neighbor 5G", "not the password")
        self.assertFalse(r["ok"])
        self.assertIn("password was not accepted", r["error"])
        self.assertTrue(r["error"].endswith("Nothing was saved."))
        self.assertNotIn("not the password", json.dumps(r))  # the fake echoes it on stderr; explain() would have to hide it
        self.assertEqual(self.f.read("deleted").split(), [UUID_NEW])
        self.assertNotIn(UUID_NEW, self.f.read("connections"))

    def test_error_text_never_carries_the_password(self):
        self.f.put("leave_profile")
        r = self.f.net().join("Neighbor 5G", "echoed-back-1")
        self.assertNotIn("echoed-back-1", json.dumps(r))

    def test_polkit_refusal_is_detected_before_trying(self):
        self.f.put("permissions", PERMS.replace("settings.modify.system:yes", "settings.modify.system:auth"))
        r = self.f.net().join("Neighbor 5G", PW)
        self.assertFalse(r["ok"])
        self.assertIn("administrator", r["error"])
        self.assertNotIn("device wifi connect", self.f.calls())

    def test_polkit_refusal_at_the_tool_is_explained(self):
        self.f.put("join_denied")
        r = self.f.net().join("Neighbor 5G", PW)
        self.assertFalse(r["ok"])
        self.assertIn("does not allow this change", r["error"])
        self.assertNotIn(PW, json.dumps(r))

    def test_bad_input_never_reaches_nmcli(self):
        n = self.f.net()
        for ssid, pw in (("", PW), ("-x", PW), ("a\nb", PW), ("Neighbor 5G", "short"), ("Neighbor 5G", "pass\nword1"), ("Neighbor 5G", "p\u00e4ssword11"), ("x" * 40, PW)):
            self.assertFalse(n.join(ssid, pw)["ok"], (ssid, pw))
        self.assertFalse(n.join("Neighbor 5G", 12345678)["ok"])
        self.assertEqual(self.f.calls(), "")

    def test_control_off_refuses(self):
        r = self.f.net(env={"OPOS_NETWORK": ""}).join("Neighbor 5G", PW)
        self.assertFalse(r["ok"])
        self.assertIn("switched off on this install", r["error"])
        self.assertEqual(self.f.calls(), "")

    def test_open_network(self):
        self.f.rm("psk")
        r = self.f.net().join("Guest Open")
        self.assertTrue(r["ok"], r)
        self.assertNotIn("--ask", self.f.read("cmdline.log"))

    def test_secured_network_without_password_asks_for_one(self):
        r = self.f.net().join("Neighbor 5G")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "This network needs a password.")
        self.assertNotIn("device wifi connect", self.f.calls())

    def test_saved_network_connects_without_a_password(self):
        r = self.f.net().join("HomeNet")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.f.read("up").split(), [UUID_HOME])
        self.assertNotIn("The password is kept", r["message"])
        r = self.f.net().join("Cafe: Free WiFi", "")  # an empty password means none
        self.assertTrue(r["ok"], r)
        self.assertIn(UUID_CAFE, self.f.read("up"))

    def test_a_new_password_replaces_the_saved_entry(self):
        self.f.put("psk", "brand new pass")
        r = self.f.net().join("HomeNet", "brand new pass")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.f.read("deleted").split(), [UUID_HOME])

    def test_a_wrong_new_password_for_a_saved_network_keeps_the_old_entry(self):
        self.f.put("leave_profile")
        r = self.f.net().join("HomeNet", "not the password")
        self.assertFalse(r["ok"])
        self.assertEqual(self.f.read("deleted").split(), [UUID_NEW], "only what the failed attempt itself left behind is deleted")
        self.assertIn(UUID_HOME, self.f.read("connections"))
        self.assertNotIn(UUID_NEW, self.f.read("connections"))
        self.assertTrue(r["error"].endswith("Your saved entry for this network was kept."), r["error"])
        self.assertNotIn("was removed", r["error"])
        self.assertNotIn("Nothing was saved", r["error"], "the old entry is still saved, so that would be false")

    def test_no_failed_join_costs_the_saved_entry(self):
        """The password was right and the join still failed (no address, the network went away, a timeout), or it was wrong."""
        causes = {
            "no address": "Error: Connection activation failed: IP configuration could not be reserved (no available address, timeout, etc.).",
            "network gone": "Error: No network with SSID 'HomeNet' found.",
            "not in range": "Error: Connection activation failed: The Wi-Fi network could not be found",
        }
        for name, text in causes.items():
            with self.subTest(name):
                f = Fake(self)
                f.put("psk", "the right one")
                f.put("activation_fails", text + "\n")
                f.put("leave_profile")
                r = f.net().join("HomeNet", "the right one")
                self.assertFalse(r["ok"])
                self.assertEqual(f.read("deleted").split(), [UUID_NEW], name)
                self.assertIn(UUID_HOME, f.read("connections"), name)
                self.assertIn("Your saved entry for this network was kept.", r["error"])
        f = Fake(self)
        f.put("psk", "the right one")
        f.put("join_hang")
        with mock.patch.object(net, "JOIN_WAIT_S", 0), mock.patch.object(net, "JOIN_EXTRA_S", 1):
            r = f.net().join("HomeNet", "the right one")
        self.assertIn("did not answer in time", r["error"])
        self.assertEqual(f.read("deleted"), "")
        self.assertIn(UUID_HOME, f.read("connections"))
        self.assertIn("Your saved entry for this network was kept.", r["error"])

    def test_a_new_password_that_works_replaces_the_old_entry_only_after_the_join(self):
        self.f.put("psk", "brand new pass")
        r = self.f.net().join("HomeNet", "brand new pass")
        self.assertTrue(r["ok"], r)
        calls = self.f.calls().splitlines()
        join_at = next(i for i, c in enumerate(calls) if "passwd-file" in c)
        delete_at = next(i for i, c in enumerate(calls) if "connection delete" in c)
        self.assertLess(join_at, delete_at, "the old entry is deleted after the new one is up, not before")
        self.assertEqual(self.f.read("deleted").split(), [UUID_HOME])
        self.assertNotIn("older saved entry", r["message"], "nothing is left over to report")

    def test_the_entry_nmcli_reports_as_activated_is_never_deleted(self):
        # an nmcli that reused the old profile instead of making a new one
        self.f.put("psk", "brand new pass")
        self.f.put("activated_uuid", UUID_HOME)
        r = self.f.net().join("HomeNet", "brand new pass")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.f.read("deleted"), "")

    def test_when_the_new_entry_cannot_be_told_the_old_one_is_kept(self):
        self.f.put("psk", "brand new pass")
        self.f.put("quiet_ok")
        r = self.f.net().join("HomeNet", "brand new pass")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.f.read("deleted"), "")
        self.assertIn("older saved entry for this network was kept", r["message"])

    def test_nothing_was_saved_is_not_claimed_when_the_leftovers_cannot_be_checked(self):
        self.f.put("leave_profile")
        self.f.put("list_fails_after", "1")  # the list before the attempt works, the one after does not
        r = self.f.net().join("Neighbor 5G", "not the password")
        self.assertFalse(r["ok"])
        self.assertNotIn("Nothing was saved", r["error"])
        self.assertIn("could not check whether the attempt left an entry behind", r["error"])

    def test_nothing_was_saved_is_not_claimed_while_a_saved_profile_cannot_be_read(self):
        # "Old hotspot" may well be a saved entry for Neighbor 5G (its SSID cannot be read), and its name is not the SSID, so it is
        # invisible to the match: the attempt's own leftover is removed, and the text must not say nothing is saved
        uid = "55555555-5555-4555-8555-555555555555"
        self.f.put("connections", self.f.read("connections") + f"Old hotspot:{uid}:wifi:\n")  # no ssid_<uuid> file: nmcli fails for it
        self.f.put("leave_profile")
        r = self.f.net().join("Neighbor 5G", "not the password")
        self.assertFalse(r["ok"])
        self.assertNotIn("Nothing was saved", r["error"])
        self.assertNotIn("kept", r["error"], "no entry was matched, so none can be said to be kept either")
        self.assertTrue(r["error"].endswith("OPOS could not read every saved network, so it cannot tell whether Neighbor 5G is saved."), r["error"])
        self.assertEqual(self.f.read("deleted").split(), [UUID_NEW], "the attempt's own leftover is still removed, and only that")
        self.assertIn(uid, self.f.read("connections"))

    def test_nothing_was_saved_is_still_said_when_every_profile_could_be_read(self):
        self.f.put("leave_profile")
        r = self.f.net().join("Neighbor 5G", "not the password")
        self.assertTrue(r["error"].endswith("Nothing was saved."), r["error"])

    def test_a_listing_that_fails_before_the_join_claims_nothing_and_deletes_nothing(self):
        self.f.put("leave_profile")
        self.f.put("list_fails_after", "0")  # NetworkManager lists nothing at all, before or after
        r = self.f.net().join("Neighbor 5G", "not the password")
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"].endswith("OPOS could not check whether the attempt left an entry behind."), r["error"])
        self.assertNotIn("Nothing was saved", r["error"])
        self.assertEqual(self.f.read("deleted"), "", "without the list from before the attempt, nothing can be told apart from what was there")
        self.assertIn(UUID_NEW, self.f.read("connections"))

    def test_hidden_network(self):
        r = self.f.net().join("Secret Net", PW, hidden=True)
        self.assertTrue(r["ok"], r)
        self.assertIn("wifi.hidden yes", self.f.read("cmdline.log"))
        self.f.net().join("Secret Net", PW, hidden="yes")  # only a real True counts
        self.assertNotIn("hidden", self.f.read("cmdline.log"))

    def test_wifi_off_asks_to_turn_it_on(self):
        self.f.put("general_status", "running:connected:none:enabled:disabled:enabled:enabled\n")
        self.assertIn("switched off", self.f.net().join("Neighbor 5G", PW)["error"])
        self.f.put("general_status", "running:connected:none:disabled:enabled:enabled:enabled\n")
        self.assertIn("hardware switch", self.f.net().join("Neighbor 5G", PW)["error"])

    def test_a_join_that_hangs_times_out_in_plain_words(self):
        self.f.put("join_hang")
        n = self.f.net()
        t = time.monotonic()
        with mock.patch.object(net, "JOIN_WAIT_S", 0), mock.patch.object(net, "JOIN_EXTRA_S", 1):
            r = n.join("Neighbor 5G", PW)
        self.assertFalse(r["ok"])
        self.assertIn("did not answer in time", r["error"])
        self.assertLess(time.monotonic() - t, 15)
        self.assertNotIn(PW, json.dumps(r))

    def test_one_change_at_a_time(self):
        n = self.f.net()
        self.assertTrue(n._busy.acquire(blocking=False))
        try:
            r = n.join("Neighbor 5G", PW)
        finally:
            n._busy.release()
        self.assertFalse(r["ok"])
        self.assertIn("still running", r["error"])

    def test_tty_mode_for_an_nmcli_that_insists_on_a_terminal(self):
        self.f.put("need_tty")
        r = self.f.net().join("Neighbor 5G", PW)  # passwd-file does not need a terminal
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(self.f.read("pwfile").strip()))
        with mock.patch.dict(os.environ, {"PATH": f"{self.f.bin}:{self.f.tools}"}):
            r = net.Net(root=self.f.root, env={"OPOS_NETWORK": "1", "OPOS_NET_TTY": "1"}, sleep=lambda s: None).join("Neighbor 5G", PW)
        self.assertTrue(r["ok"], r)
        self.assertNotIn(PW, self.f.read("cmdline.log"))
        self.assertNotIn(PW, json.dumps(r))

    def test_tty_mode_times_out_and_reports_missing_tools(self):
        self.f.put("join_hang")
        r = net.run(["nmcli", "--ask", "device", "wifi", "connect", "x"], 0.5, "pw\n", path=f"{self.f.bin}:{self.f.tools}", tty=True)
        self.assertTrue(r.timed_out)
        r = net.run(["nope-not-here"], 0.5, "pw\n", path=str(self.f.tools), tty=True)
        self.assertTrue(r.missing)


UUID_DUP = "77777777-7777-4777-8777-777777777777"


def crowded(f, extra=22, unreadable=()):
    """25 saved Wi-Fi profiles in all: HomeNet and Cafe, `extra` fillers, and a second HomeNet entry that is the 25th, past the
    twentieth that a list cut for showing keeps. `unreadable` names fillers whose SSID nmcli will not give."""
    lines = [CONNS.rstrip("\n")]
    for i in range(1, extra + 1):
        u = "aaaaaaaa-0000-4000-8000-%012d" % i
        lines.append(f"Filler {i:02d}:{u}:wifi:")
        if f"Filler {i:02d}" not in unreadable:
            f.put(f"ssid_{u}", f"802-11-wireless.ssid:Filler {i:02d}\n")
    lines.append(f"HomeNet backup:{UUID_DUP}:wifi:")
    f.put(f"ssid_{UUID_DUP}", "802-11-wireless.ssid:HomeNet\n")
    f.put("connections", "\n".join(lines) + "\n")
    return f


class LargeInventory(unittest.TestCase):
    """More saved networks than a list cut for showing keeps: what is saved is decided from all of them."""

    def test_the_fixture_has_25_wifi_profiles_and_the_second_homenet_is_the_25th(self):
        f = crowded(Fake(self))
        wifi = [ln for ln in f.read("connections").splitlines() if ":wifi:" in ln]
        self.assertEqual(len(wifi), 25)
        self.assertTrue(wifi[24].endswith(f"{UUID_DUP}:wifi:"))
        self.assertEqual(sum(ln.startswith("HomeNet") for ln in wifi), 2)

    def test_forget_removes_both_entries_for_the_ssid_and_says_so_only_then(self):
        f = crowded(Fake(self))
        r = f.net().forget("HomeNet")
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(f.read("deleted").split()), sorted([UUID_HOME, UUID_DUP]))
        self.assertIn("Its password is gone", r["message"])
        left = f.read("connections")
        self.assertNotIn(UUID_HOME, left)
        self.assertNotIn(UUID_DUP, left)
        self.assertIn(UUID_CAFE, left)

    def test_forget_looks_again_and_does_not_claim_what_it_cannot_check(self):
        f = crowded(Fake(self))
        f.put("list_fails_after", "1")  # the first list works; the one after the deletions does not
        r = f.net().forget("HomeNet")
        self.assertFalse(r["ok"])
        self.assertNotIn("password is gone", json.dumps(r))
        self.assertIn("may remain", r["error"])
        self.assertEqual(sorted(f.read("deleted").split()), sorted([UUID_HOME, UUID_DUP]))

    def test_an_unreadable_profile_means_forget_cannot_claim_completion(self):
        f = crowded(Fake(self), unreadable=("Filler 05",))
        r = f.net().forget("HomeNet")
        self.assertFalse(r["ok"], r)
        self.assertNotIn("password is gone", json.dumps(r))
        self.assertIn("may remain", r["error"])
        self.assertEqual(sorted(f.read("deleted").split()), sorted([UUID_HOME, UUID_DUP]), "what was readable is still deleted")

    def test_an_unreadable_profile_does_not_turn_not_found_into_not_saved(self):
        f = crowded(Fake(self), unreadable=("Filler 05",))
        r = f.net().forget("Nowhere")
        self.assertFalse(r["ok"])
        self.assertNotEqual(r["error"], "That network is not saved on this phone.")
        self.assertIn("could not read every saved network", r["error"])
        self.assertEqual(f.read("deleted"), "")

    def test_forget_never_deletes_a_profile_it_only_guessed_by_name(self):
        # a profile named like the network whose SSID could not be read may be a different network
        f = crowded(Fake(self), unreadable=("Filler 05",))
        r = f.net().forget("Filler 05")
        self.assertFalse(r["ok"])
        self.assertEqual(f.read("deleted"), "")

    def test_a_stray_profile_past_the_twentieth_is_cleaned_after_a_failed_join(self):
        f = crowded(Fake(self))
        f.put("psk", PW)
        f.put("leave_profile")  # the failed attempt adds its profile at the END of the list
        r = f.net().join("Neighbor 5G", "not the password")
        self.assertFalse(r["ok"])
        self.assertEqual(f.read("deleted").split(), [UUID_NEW])
        self.assertNotIn(UUID_NEW, f.read("connections"))
        self.assertTrue(r["error"].endswith("Nothing was saved."), r["error"])

    def test_a_saved_network_past_the_twentieth_is_still_a_saved_network(self):
        f = crowded(Fake(self))
        f.put("connections", f.read("connections").replace("HomeNet backup", "Backup Net"))
        f.put(f"ssid_{UUID_DUP}", "802-11-wireless.ssid:Backup Net\n")
        r = f.net().join("Backup Net")  # no password: it is saved, so it is brought up
        self.assertTrue(r["ok"], r)
        self.assertEqual(f.read("up").split(), [UUID_DUP])
        f.put("psk", "brand new pass")
        r = f.net().join("Backup Net", "brand new pass")
        self.assertTrue(r["ok"], r)
        self.assertEqual(f.read("deleted").split(), [UUID_DUP], "the old entry is replaced, not left beside the new one")

    def test_the_list_cut_for_showing_is_still_cut(self):
        f = crowded(Fake(self))
        n = f.net()
        q = net._Q(n, 60)
        self.assertEqual(len(n._saved_wifi(q)), net.PROFILE_MAX)
        profiles, complete = n._wifi_profiles(q)
        self.assertEqual(len(profiles), 25)
        self.assertTrue(complete)
        self.assertFalse(n._wifi_profiles(q, net.PROFILE_MAX)[1], "a cut list is never complete")


class ForgetAndWifi(unittest.TestCase):
    def test_forget_deletes_every_entry_for_the_ssid(self):
        f = Fake(self)
        f.put("connections", CONNS + f"HomeNet 1:55555555-5555-4555-8555-555555555555:wifi:\n")
        f.put("ssid_55555555-5555-4555-8555-555555555555", "802-11-wireless.ssid:HomeNet\n")
        r = f.net().forget("HomeNet")
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(f.read("deleted").split()), sorted([UUID_HOME, "55555555-5555-4555-8555-555555555555"]))
        self.assertIn("Its password is gone", r["message"])
        self.assertNotIn(UUID_GSM, f.read("deleted"))

    def test_forget_with_a_colon_in_the_name(self):
        f = Fake(self)
        self.assertTrue(f.net().forget("Cafe: Free WiFi")["ok"])
        self.assertEqual(f.read("deleted").split(), [UUID_CAFE])

    def test_forget_unknown_and_refused(self):
        f = Fake(self)
        r = f.net().forget("Nowhere")
        self.assertEqual((r["ok"], r["error"]), (False, "That network is not saved on this phone."))
        f.put("permissions", PERMS.replace("settings.modify.system:yes", "settings.modify.system:no"))
        self.assertIn("does not let OPOS", f.net().forget("HomeNet")["error"])
        self.assertEqual(f.read("deleted"), "")
        self.assertFalse(f.net().forget("")["ok"])

    def test_forget_works_with_wifi_off(self):
        f = Fake(self)
        f.put("general_status", "running:connected:none:enabled:disabled:enabled:enabled\n")
        self.assertTrue(f.net().forget("HomeNet")["ok"])

    def test_power_and_disconnect(self):
        f = Fake(self)
        r = f.net().wifi("off")
        self.assertTrue(r["ok"], r)
        self.assertEqual(f.read("radio").strip(), "off")
        self.assertEqual(r["message"], "Wi-Fi is off.")
        self.assertTrue(f.net().wifi("on")["ok"])
        self.assertEqual(f.read("radio").strip(), "on")
        r = f.net().wifi("disconnect")
        self.assertTrue(r["ok"], r)
        self.assertEqual(f.read("disc").strip(), "wlan0")
        self.assertIn("will not rejoin", r["message"])
        self.assertFalse(f.net().wifi("reboot")["ok"])

    def test_power_refused_and_hardware_blocked(self):
        f = Fake(self)
        f.put("permissions", PERMS.replace("enable-disable-wifi:yes", "enable-disable-wifi:auth"))
        self.assertIn("administrator", f.net().wifi("off")["error"])
        self.assertEqual(f.read("radio"), "")
        f.put("permissions", PERMS)
        f.put("general_status", "running:connected:none:disabled:enabled:enabled:enabled\n")
        r = f.net().wifi("on")
        self.assertFalse(r["ok"])
        self.assertIn("hardware switch", r["error"])

    def test_everything_off_without_control(self):
        f = Fake(self)
        n = f.net(env={"OPOS_NETWORK": ""})
        for r in (n.join("HomeNet"), n.forget("HomeNet"), n.wifi("off")):
            self.assertFalse(r["ok"])
        self.assertEqual(f.calls(), "")


class Web(unittest.TestCase):
    def setUp(self):
        self.f = Fake(self)
        self.f.put("psk", PW)
        net_page.set_service(self.f.net(env=os.environ))
        self.addCleanup(net_page.set_service, None)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPOS_NETWORK", None)

    def get(self, port, path="/network"):
        return request(port, "GET", path)

    def test_page_is_served_with_a_token_nonce_and_the_honest_text(self):
        with Running() as s:
            status, hdrs, body = self.get(s.port)
            self.assertEqual(status, 200)
            self.assertIn("text/html", hdrs["content-type"])
            html = body.decode()
            self.assertIn(f'<meta name="opos-token" content="{s.token}">', html)
            csp = hdrs["content-security-policy"]
            nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp).group(1)
            self.assertIn(f'<script nonce="{nonce}">', html)
            self.assertIn(f'<style nonce="{nonce}">', html)
            self.assertEqual(html.count("<script"), 2)  # the theme loader in the head and the page script, both under the nonce
            self.assertEqual(html.count(f'<script nonce="{nonce}">'), 2)
            self.assertNotRegex(html, r"\son[a-z]+=")
            self.assertNotIn("innerHTML", html)
            self.assertIn("not kept by OPOS", html)
            self.assertIn('type="password"', html)
            self.assertIn("--hinge:42px", html)
            self.assertIn("[hidden]{display:none!important}", html)  # labels set display, which would otherwise show their hidden rows
            self.assertIn('href="/"', html)
            self.assertEqual(fetch_token(s.port), s.token)
            h = request(s.port, "HEAD", "/network")
            self.assertEqual(h[0], 200)
            self.assertEqual(h[2], b"")
            self.assertEqual(request(s.port, "GET", "/network", headers={"Host": "evil.example"})[0], 403)

    def test_the_page_stays_small_and_separate_from_the_main_page(self):
        self.assertLess(len(net_page.render("t" * 43, "n" * 22).encode()), 12000)
        self.assertNotIn("net_status", web.page("t" * 43, "n" * 22))

    def test_status_over_the_api(self):
        with Running() as s:
            code, hdrs, j = post_action(s.port, "net_status")
            self.assertEqual(code, 200)
            self.assertEqual(j["wifi"]["ssid"], "HomeNet")
            self.assertIn("still simulated", j["simulated"])
            self.assertFalse(j["control"]["enabled"])  # OPOS_NETWORK is not set in this environment
            self.assertEqual(post_action(s.port, "net_status", token="x" * 43)[0], 403)

    def test_actions_need_the_token_and_the_origin(self):
        with Running() as s:
            body = json.dumps({"action": "net_status"}).encode()
            base = {"Host": f"127.0.0.1:{s.port}", "Content-Type": "application/json"}
            self.assertEqual(request(s.port, "POST", "/api", body, base)[0], 403)
            self.assertEqual(request(s.port, "POST", "/api", body, {**base, "X-OPOS-Token": "wrong" * 9})[0], 403)
            bad = {**base, "X-OPOS-Token": s.token, "Origin": "http://evil.example"}
            self.assertEqual(request(s.port, "POST", "/api", body, bad)[0], 403)
            ok = {**base, "X-OPOS-Token": s.token, "Origin": f"http://127.0.0.1:{s.port}"}
            self.assertEqual(request(s.port, "POST", "/api", body, ok)[0], 200)
            self.assertEqual(request(s.port, "POST", "/api", body, {**ok, "Content-Type": "text/plain"})[0], 415)

    def test_join_over_the_api_never_echoes_the_password(self):
        os.environ["OPOS_NETWORK"] = "1"
        with Running() as s:
            code, _, j = post_action(s.port, "net_join", ssid="Neighbor 5G", password=PW)
            self.assertEqual(code, 200)
            self.assertTrue(j["ok"], j)
            self.assertNotIn(PW, json.dumps(j))
            code, _, j = post_action(s.port, "net_join", ssid="Neighbor 5G", password=WRONG)
            self.assertFalse(j["ok"])
            self.assertIn("password was not accepted", j["error"])
            self.assertNotIn(PW, self.f.calls())
            self.assertNotIn(WRONG, self.f.calls())

    def test_payload_shapes_are_checked(self):
        svc = self.f.net()
        for fields in ({"ssid": 5}, {}, {"ssid": "x" * 65}, {"ssid": "ok", "password": 7}, {"ssid": "ok", "password": "p" * 129}, {"ssid": "ok", "hidden": "yes"}):
            self.assertEqual(net_page.handle("net_join", fields, svc)[0], 400, fields)
        self.assertEqual(net_page.handle("net_forget", {}, svc)[0], 400)
        self.assertEqual(net_page.handle("net_wifi", {"mode": 3}, svc)[0], 400)
        self.assertEqual(net_page.handle("net_other", {}, svc)[0], 400)
        code, j = net_page.handle("net_wifi", {"mode": "reboot"}, svc)
        self.assertEqual((code, j["ok"]), (200, False))
        with Running() as s:
            self.assertEqual(post_action(s.port, "net_join", ssid=5)[0], 400)

    def test_actions_are_rate_limited(self):
        with Running() as s:
            codes = [post_action(s.port, "net_scan", rescan=False)[0] for _ in range(4)]
            self.assertEqual(codes[:2], [200, 200])
            self.assertEqual(codes[-1], 429)
            codes = [post_action(s.port, "net_join", ssid="Neighbor 5G")[0] for _ in range(5)]
            self.assertEqual(codes[-1], 429)

    def test_control_is_off_unless_the_install_turns_it_on(self):
        with Running() as s:
            for a, f in (("net_join", {"ssid": "HomeNet"}), ("net_forget", {"ssid": "HomeNet"}), ("net_wifi", {"mode": "off"})):
                code, _, j = post_action(s.port, a, **f)
                self.assertEqual((code, j["ok"]), (200, False), a)
                self.assertIn("switched off on this install", j["error"])
        self.assertNotIn("connection delete", self.f.calls())
        self.assertNotIn("radio", self.f.calls())

    def test_network_names_stay_out_of_the_phone_state_and_the_helper(self):
        os.environ["OPOS_NETWORK"] = "1"
        phone = web.Phone(visible=True)
        with Running(phone=phone) as s:
            post_action(s.port, "net_status")
            post_action(s.port, "net_scan")
            _, _, state = post_action(s.port, "tick", real_s=1)
        blob = json.dumps(state) + json.dumps(phone.ask_facts()) + json.dumps(phone.state())
        for word in ("HomeNet", "Neighbor 5G", "Cafe", "198.51.100.23", "203.0.113.1", "Example Mobile", "net_status"):
            self.assertNotIn(word, blob)
        for mod in ("assist", "muse", "advisor", "router", "governor"):
            self.assertNotIn("opos.net", (ROOT / "src" / "opos" / f"{mod}.py").read_text())
            self.assertNotRegex((ROOT / "src" / "opos" / f"{mod}.py").read_text(), r"from opos import [^\n]*\bnet\b")

    def test_main_page_is_unchanged_unless_the_duo_os_asks(self):
        base = web.page("t" * 43, "n" * 22)
        with mock.patch.dict(os.environ, {"OPOS_NETWORK": ""}):
            self.assertEqual(web.page("t" * 43, "n" * 22), base)
        with mock.patch.dict(os.environ, {"OPOS_NETWORK": "0"}):
            self.assertEqual(web.page("t" * 43, "n" * 22), base)
        self.assertNotIn("/network", base)
        with mock.patch.dict(os.environ, {"OPOS_NETWORK": "1"}):
            on = web.page("t" * 43, "n" * 22)
        self.assertIn('<a id="net-link" href="/network"', on)
        self.assertEqual(on.count("/network"), 1)
        self.assertEqual(on.replace(net_page.LINK, ""), base)
        self.assertLess(len(net_page.LINK), 300)
        self.assertEqual(net_page.add_settings_link("<p>no anchor</p>", {"OPOS_NETWORK": "1"}), "<p>no anchor</p>")


class Cli(unittest.TestCase):
    def test_probe_and_status_run_without_raising(self):
        f = Fake(self)
        f.put("psk", PW)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PATH=f"{f.bin}:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run(["python3", "-m", "opos.net", "probe"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("$ nmcli -t -f RUNNING", p.stdout)
        self.assertIn("rc=0", p.stdout)
        p = subprocess.run(["python3", "-m", "opos.net", "status"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["wifi"]["ssid"], "HomeNet")
        p = subprocess.run(["python3", "-m", "opos.net", "join", "Neighbor 5G"], input="Wrong password\n", capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Wrong password", p.stdout + p.stderr)


class Packaging(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_polkit_rule_grants_exactly_what_the_code_needs(self):
        rule = self.read("packaging/duo-os/50-opos-network.rules")
        ids = set(re.findall(r'"(org\.freedesktop\.NetworkManager\.[a-z.-]+)"', rule))
        self.assertEqual(ids, set(net.ACTIONS))
        self.assertIn('subject.user == "droidian"', rule)
        self.assertNotIn("NetworkManager.*", rule)
        self.assertIn("polkit.Result.YES", rule)
        self.assertEqual(rule.count("polkit.addRule"), 1)

    def test_systemd_dropin_turns_control_on(self):
        self.assertEqual([l for l in self.read("packaging/duo-os/opos-network.conf").splitlines() if l and not l.startswith("#")],
                         ["[Service]", "Environment=OPOS_NETWORK=1"])

    def test_image_build_leaves_the_rule_out_unless_asked(self):
        b = self.read("packaging/duo-os/build-image.sh")
        blk = b[b.index("# --- track: network"):b.index("# --- end track: network")]
        self.assertIn("opos-network.conf", blk)
        self.assertRegex(blk, r'if \[ "\$\{OPOS_NET_POLKIT:-0\}" = 1 \]')
        self.assertNotIn("OPOS_NET_POLKIT:-1", blk)
        self.assertLess(blk.index("OPOS_NET_POLKIT"), blk.index("50-opos-network.rules"))
        rule = self.read("packaging/duo-os/50-opos-network.rules")
        self.assertIn('subject.system_unit == "opos-server.service"', rule)
        self.assertIn('subject.user == "droidian"', rule)
        for script in ("build-image.sh", "verify-image.sh", "opos-diag"):
            p = subprocess.run(["sh", "-n", str(ROOT / "packaging/duo-os" / script)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, script + p.stderr)

    def test_diag_has_one_read_only_radios_block(self):
        d = self.read("packaging/duo-os/opos-diag")
        blk = d[d.index("# --- track: network"):d.index("# --- end track: network")]
        self.assertEqual(blk.count('echo "== radios'), 1)
        for tool in ("rfkill list", "nmcli", "mmcli", "bluetoothctl show", "general permissions"):
            self.assertIn(tool, blk)
        for forbidden in ("connection up", "connection delete", "radio wifi", "rfkill block", "rfkill unblock", "device wifi connect", "ssh", "password"):
            self.assertNotIn(forbidden, blk)
        self.assertNotIn("CONNECTION", blk)  # profile names are SSIDs: the dump leaves them out
        self.assertEqual(d.count("# --- track: network"), 1)

    def test_diag_block_runs_when_the_tools_are_missing_or_fake(self):
        d = self.read("packaging/duo-os/opos-diag")
        blk = d[d.index("# --- track: network"):d.index("# --- end track: network")]
        f = Fake(self)
        script = f.dir / "radios.sh"
        script.write_text("#!/bin/sh\n" + blk + "\n", newline="\n")
        for path in (f"{f.bin}:{f.tools}", str(f.dir / "empty")):
            p = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, env={"PATH": path}, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("== radios", p.stdout)
            self.assertNotIn("356938035643809", p.stdout)
            self.assertNotIn("5C:BA:37", p.stdout)
        p = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, env={"PATH": f"{f.bin}:{f.tools}"}, timeout=60)
        self.assertIn("registered", p.stdout)
        self.assertIn("lte", p.stdout)

    def test_no_secret_or_ssid_ships_in_the_repo_files(self):
        for name in ("src/opos/net.py", "src/opos/net_page.py", "packaging/duo-os/50-opos-network.rules", "packaging/duo-os/opos-network.conf", "docs/network.md"):
            t = self.read(name)
            self.assertNotRegex(t, r"(?i)psk\s*=\s*\S|password\s*=\s*['\"][^'\"]+['\"]", name)

    def test_docs_state_the_limits(self):
        d = self.read("docs/network.md")
        for phrase in ("still simulated", "OPOS_NETWORK", "OPOS_NET_POLKIT", "nmcli --ask", "not tested on the phone", "802.1X"):
            self.assertIn(phrase, d)
        flat = " ".join(d.split())
        # the accepted posture: one owner, a token any local process can read; and how far the optional rule reaches
        for phrase in ("any local user or process on the phone can read it and change Wi-Fi", "single-user phone",
                       "activate, modify and delete every NetworkManager connection, mobile data and VPN profiles included",
                       "The page token is served on `GET /network`",
                       "keeps the old entry", "A radio whose state was not read keeps the answer at \"unknown\""):
            self.assertIn(phrase, flat)
        self.assertIn("network.md", self.read("docs/README.md"))


if __name__ == "__main__":
    unittest.main()
