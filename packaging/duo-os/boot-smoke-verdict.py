#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The judge of a boot smoke run: reads a serial log and says which positive line it requires.

    python3 -I -B boot-smoke-verdict.py --log SERIAL.log --accept server-started
    python3 -I -B boot-smoke-verdict.py --log SERIAL.log --expect IDENTITY

--accept server-started takes the positive line from a source the image already ships: systemd's own console line
for opos-server.service, '[  OK  ] Started opos-server.service - <Description>.' (systemd 253 and later) or
'[  OK  ] Started <Description>.' (earlier), with <Description> read from the opos-server.service next to this file.
It says the server process was started; it does not name the image and does not show that the server answers.
--expect IDENTITY requires the documented marker 'OPOS_BOOT_OK <IDENTITY> server=ok' instead, IDENTITY being the
SHA-256 of the image's /etc/opos/image.json (64 lowercase hex digits). No unit in this repository prints that marker
yet: the unit that would (opos-boot-ok.service) is a proposal that needs Louis's yes (packaging/duo-os/README.md,
"Boot smoke test (QEMU, PC only)").
Exit 0 only for exactly one positive line, alone on its line, and no negative marker anywhere in the log; exit 1 for a
negative marker, a wrong identity, a malformed marker or a repeated positive line; exit 3 for no positive line (the
guest timed out or said nothing); exit 2 for bad arguments. One JSON line of evidence on stdout.
Standard library only; it reads the log and the unit file and writes nothing."""
import argparse
import hashlib
import json
import os
import re
import sys

IDENTITY = re.compile(r"[0-9a-f]{64}")
# The documented positive line, alone on its line.
MARKER = re.compile(r"OPOS_BOOT_OK ([0-9a-f]{64}) server=ok")
# Any line that starts with a marker word but is not exactly the positive line is a failure, never ignored.
MARKER_START = ("OPOS_BOOT_OK", "OPOS_BOOT_FAIL")
# Failure text of the kernel, systemd and OPOS. Matched anywhere in a line and in any case: more matches only fail more.
NEGATIVE = ("Kernel panic", "Failed to start", "emergency mode", "Dependency failed", "opos-fallback", "OPOS_BOOT_FAIL")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]")
SERVER_UNIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opos-server.service")
KEEP = 20


class Args(argparse.ArgumentParser):
    def error(self, message):
        finish(2, "bad-arguments", reason=message)


def finish(code, word, **fields):
    print(json.dumps(dict({"tool": "boot-smoke-verdict", "verdict": word, "exit": code}, **fields), sort_keys=True))
    sys.exit(code)


def server_started_lines(unit_text):
    """The two console lines systemd prints when it has started opos-server.service, from the unit's Description=."""
    desc = [ln[len("Description="):] for ln in unit_text.splitlines() if ln.startswith("Description=")]
    if len(desc) != 1 or not desc[0].strip():
        raise ValueError("opos-server.service must have exactly one non-empty Description=")
    return ("[  OK  ] Started opos-server.service - %s." % desc[0], "[  OK  ] Started %s." % desc[0])


def judge(data, expect=None, started=None):
    """(exit code, verdict word, evidence fields) for the bytes of a serial log.

    EXPECT is the identity for the documented marker; STARTED, when given instead, is the pair of server-started lines."""
    text = ANSI.sub("", data.decode("utf-8", "replace"))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    markers, malformed, negative, positive = [], [], [], []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        m = MARKER.fullmatch(line)
        if m:
            markers.append({"line": n, "identity": m.group(1)})
        elif line.startswith(MARKER_START):
            malformed.append({"line": n, "text": line[:160]})
        if started is not None and line in started:
            positive.append({"line": n})
        low = line.lower()
        for word in NEGATIVE:
            if word.lower() in low:
                negative.append({"line": n, "marker": word, "text": line[:160]})
    ev = {"markers": markers[:KEEP], "malformed": malformed[:KEEP], "negative": negative[:KEEP], "lines": len(lines)}
    if started is not None:
        ev["server_started"] = positive[:KEEP]
    if negative:
        return 1, "fail", dict(ev, reason="a negative marker is in the log")
    if malformed:
        return 1, "fail", dict(ev, reason="a marker line that is not exactly 'OPOS_BOOT_OK <identity> server=ok'")
    if started is not None:
        if not positive:
            return 3, "timeout", dict(ev, reason="no 'Started opos-server.service' line: the guest timed out or said nothing")
        if len(positive) > 1:
            return 1, "fail", dict(ev, reason="opos-server.service was started more than once: it restarted")
        return 0, "pass", dict(ev, reason="systemd started opos-server.service once and no negative marker; the image identity is not checked")
    if not markers:
        return 3, "timeout", dict(ev, reason="no OPOS_BOOT_OK line: the guest timed out or said nothing")
    if len(markers) > 1:
        return 1, "fail", dict(ev, reason="more than one OPOS_BOOT_OK line: one boot prints one")
    if markers[0]["identity"] != expect:
        return 1, "fail", dict(ev, reason="the marker names another image")
    return 0, "pass", dict(ev, reason="one OPOS_BOOT_OK line for the expected image and no negative marker")


def main(argv=None):
    ap = Args(description="Judge a boot smoke serial log. Exit 0 pass, 1 fail, 3 timeout, 2 bad arguments.")
    ap.add_argument("--log", required=True, help="the serial log QEMU wrote (-serial file:LOG)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--accept", choices=("server-started",), help="take systemd's 'Started opos-server.service' line")
    src.add_argument("--expect", help="require the documented marker for this SHA-256 of the image's /etc/opos/image.json")
    a = ap.parse_args(argv)
    started = None
    if a.expect is not None and not IDENTITY.fullmatch(a.expect):
        finish(2, "bad-arguments", reason="--expect must be 64 lowercase hex digits (the SHA-256 of /etc/opos/image.json)")
    if a.accept == "server-started":
        try:
            with open(SERVER_UNIT, encoding="utf-8") as f:
                started = server_started_lines(f.read())
        except (OSError, ValueError) as e:
            finish(2, "bad-arguments", reason="cannot read the server unit's Description=: %s" % e, unit=SERVER_UNIT)
    try:
        with open(a.log, "rb") as f:
            data = f.read()
    except OSError as e:
        finish(2, "bad-arguments", reason="cannot read the log: %s" % e.strerror, log=a.log)
    code, word, ev = judge(data, a.expect, started)
    finish(code, word, source=a.accept or "marker", expected=a.expect, log=a.log,
           log_sha256=hashlib.sha256(data).hexdigest(), **ev)


if __name__ == "__main__":
    main()
