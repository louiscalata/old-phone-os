#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo image's service graph as data, read from the files that build it.

Reads every *.service, *.path and *.timer under packaging/duo-os/ and packaging/duo-os/updater/, the drop-ins
build-image.sh installs from this folder, and the install, ln, rm and chmod lines of build-image.sh. From them it builds
the ordering graph (After=, Before=, a path or timer unit before the unit it starts, a target after the units it wants)
and the pull-in graph (the .wants links, Wants=, Requires=, BindsTo=, OnFailure=, a path or timer unit's Unit=). Two
tables are declared here: INHERITED, the units OPOS depends on but does not ship, and FAILURE_CLASS, what a failure
of each OPOS unit does.

    python3 packaging/duo-os/bootgraph.py --check     every check below; exit 1 and one line per problem
    python3 packaging/duo-os/bootgraph.py --print     the boot order, by level (units on one level may start together)
    python3 packaging/duo-os/bootgraph.py --mermaid   the boot graph for docs/os-architecture.md
    python3 packaging/duo-os/bootgraph.py --layers    the layer diagram for docs/os-architecture.md

It reads files only. It does not run systemd: the order systemd computes on the booted phone is checked there
(systemd-analyze verify and critical-chain, the PC session), and until then that check is NOT_RUN. The graph is a
model of systemd's rules, not systemd: DefaultDependencies= ordering against basic.target and shutdown.target is left
out, and the order between the inherited targets is the summary in INHERITED_ORDER.
"""
import argparse
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG_REL = Path("packaging") / "duo-os"
DOC_REL = Path("docs") / "os-architecture.md"
UNIT_DIR = "/etc/systemd/system/"
UNIT_SUFFIXES = (".service", ".path", ".timer")
# A path or a timer unit starts one other unit: the one its [Path] or [Timer] Unit= names, else its own name as a
# .service.
TRIGGER_SECTIONS = {".path": "Path", ".timer": "Timer"}
UNIT_NAME = r"[A-Za-z0-9_@:-]+(?:\.[A-Za-z0-9_-]+)*\.(?:service|target|socket|scope|path|timer|mount|slice)"
DEP_KEYS = ("After", "Before", "Requires", "Requisite", "BindsTo", "Wants", "PartOf", "OnFailure", "Conflicts")
INSTALL_KEYS = ("WantedBy", "RequiredBy")
EXEC_KEYS = ("ExecCondition", "ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop", "ExecStopPost")
# Paths an OPOS unit may run that OPOS itself installs; anything else (python3, sh, capsh, the port's item scripts)
# comes with the base.
OPOS_PATH = re.compile(r"/usr/libexec/opos(?:-updater)?/[A-Za-z0-9_.-]+|/usr/sbin/opos-[A-Za-z0-9_.-]+")
PY_MODULE = re.compile(r"(?:^|\s)-m\s+(opos(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

# Units OPOS references but does not ship, and who provides them.
INHERITED = {
    "local-fs.target": "systemd (Debian): local file systems mounted",
    "multi-user.target": "systemd (Debian): the non-graphical system is up",
    "graphical.target": "systemd (Debian): the default target on the image",
    "network.target": "systemd (Debian): network management started (not: a network is up)",
    "dbus.socket": "dbus (Debian): the system bus socket",
    "systemd-user-sessions.service": "systemd (Debian): logins allowed",
    "getty@tty1.service": "systemd (Debian): the text console OPOS replaces (Conflicts=)",
    "rc-local.service": "systemd (Debian): /etc/rc.local, when the base has one",
    "plymouth-quit-wait.service": "plymouth (Debian): the boot splash has ended",
    "session-c1.scope": "logind: the first login session's scope, as the port's phosh unit orders after it",
    "timekeeper.service": "Droidian port: sets the clock from the hardware clock and a stored offset (sysinit)",
    "item-clock-seed.service": "Droidian port (item): seeds the stored clock offset from Android's",
    "lxc@android.service": "Droidian/Halium: the Android vendor container (hwcomposer, panels, touch, modem, audio)",
    "android-service@hwcomposer.service": "Droidian/Halium: the vendor display composer inside the container",
    "phosh.service": "Phosh project: the port's phone shell; OPOS's fallback for one boot (Conflicts=, opos-fallback)",
    "item.service": "Droidian port (item): the port's own shell unit, replaced by OPOS (Conflicts=)",
    "sensorfwd.service": "Droidian port: the sensor service, shipped disabled; opos-sensorfwd-delayed.service starts it "
                         "once and OPOS adds only the drop-in sensorfwd.service.d/70-opos-boot.conf",
    "timers.target": "systemd (Debian): the target that enabled timers are linked into (basic.target wants it)",
}

# Order between inherited units that the unit files rely on, summarized from systemd's standard targets
# (sysinit.target and basic.target sit between the first pair and are left out).
INHERITED_ORDER = (
    ("local-fs.target", "multi-user.target"),
    ("multi-user.target", "graphical.target"),
)
# graphical.target, the default target, pulls in multi-user.target (systemd's own unit says so).
DEFAULT_TARGET = "graphical.target"
# multi-user.target requires basic.target, which wants timers.target (basic.target is left out, as above; basic.target
# is not ordered after timers.target, so no order is summarized for it).
INHERITED_PULLS = (("graphical.target", "multi-user.target"), ("multi-user.target", "timers.target"))

FATAL = "fatal-to-session"
DEGRADE = "degrade"
CLASSES = (FATAL, DEGRADE)
# What a failure of each OPOS unit does. fatal-to-session: the phone has no working OPOS interface (the session unit
# fails and opos-fallback starts phosh for this boot, or the session runs with nothing to show). degrade: the
# interface still comes up, without that one part.
FAILURE_CLASS = {
    "opos.service": (FATAL, "the session (opos-session, phoc, the kiosk): it restarts; at its start limit or exit 75 "
                            "OnFailure= starts opos-fallback"),
    "opos-server.service": (FATAL, "the page and every API: Restart=always; opos-kiosk waits for it, then starts "
                                   "Chromium anyway, so the session stays up with nothing to show"),
    "opos-update-boot.service": (DEGRADE, "the staged update is not applied; the installed release keeps running"),
    "opos-update-confirm.service": (DEGRADE, "a boot-applied update is not confirmed (docs/ota-update.md)"),
    "opos-update.path": (DEGRADE, "delivered updates are not noticed until the next boot"),
    "opos-update.service": (DEGRADE, "one delivered update is not applied"),
    "opos-clock.service": (DEGRADE, "the clock is not raised to the floor (a RAM-booted Duo reads 1970)"),
    "opos-clock-set.path": (DEGRADE, "dev images only: a time file from the PC is not noticed"),
    "opos-clock-set.service": (DEGRADE, "dev images only: a time file from the PC is not applied"),
    "opos-brightness.service": (DEGRADE, "the two panels keep the base's backlight values"),
    "opos-powerkey.service": (DEGRADE, "the power key does not blank the panels; it restarts with the session"),
    "opos-volumekey.service": (DEGRADE, "the volume keys do nothing; it restarts with the session"),
    "opos-osk.service": (DEGRADE, "no port keyboard; the in-page keyboard is the default anyway"),
    "opos-diag.service": (DEGRADE, "no /var/log/opos-boot-diag.txt for this boot; the journal remains"),
    "opos-fallback.service": (DEGRADE, "runs only after the session failed; if it fails too, the screen stays on "
                                       "the failed session (there is no second fallback)"),
    "opos-sensorfwd-delayed.timer": (DEGRADE, "sensorfwd is not started this boot; nothing else waits for it"),
    "opos-sensorfwd-delayed.service": (DEGRADE, "sensorfwd is not started this boot; nothing else waits for it"),
}

# Units that build-image.sh installs or enables only when one build input is set, and that input. Every other unit is
# installed and enabled in every image; a unit whose install or link sits under an `if` must be listed here.
BUILD_CONDITIONAL = {"opos-clock-set.path": "OPOS_DEV_SSH_PUBKEY"}


def _allowed(name, condition):
    return condition is None or (name in BUILD_CONDITIONAL and BUILD_CONDITIONAL[name] in condition)

# The order the boot is written to have: (earlier, later). check_order() reports each pair the files do not give.
BOOT_ORDER = (
    ("opos-update-boot.service", "opos-server.service"),
    ("opos-update-boot.service", "opos.service"),
    ("opos-clock.service", "opos-server.service"),
    ("timekeeper.service", "opos-clock.service"),
    ("lxc@android.service", "opos-server.service"),
    ("opos-server.service", "opos.service"),
    ("android-service@hwcomposer.service", "opos.service"),
    ("opos.service", "opos-powerkey.service"),
    ("opos.service", "opos-volumekey.service"),
    ("opos.service", "opos-osk.service"),
    ("opos-server.service", "opos-update-confirm.service"),
)

# The layer diagram of docs/os-architecture.md: (from, to), an arrow means "depends on".
LAYER_NODES = {
    "apps": "Apps and screens (OPOS)",
    "shell": "Native shell, GTK 4 (OPOS)",
    "kiosk": "Browser kiosk, fallback (OPOS)",
    "session": "opos.service and opos-session (OPOS)",
    "server": "Policy server, 127.0.0.1:8765 (OPOS)",
    "updater": "Updater (OPOS)",
    "profile": "Device profile (OPOS)",
    "phoc": "phoc compositor (Phosh project)",
    "vendor": "Vendor layer: hwcomposer, panels, touch (Droidian/Halium)",
    "services": "NetworkManager, ModemManager, dbus (Debian/Droidian)",
    "systemd": "systemd, journald, logind (Debian/Droidian)",
    "kernel": "Linux 4.14 and device tree (Droidian/Halium)",
    "boot": "Boot chain: firmware, boot.img, RAM boot (PC session)",
}
LAYER_EDGES = (
    ("apps", "shell"), ("shell", "server"), ("shell", "phoc"), ("kiosk", "server"), ("kiosk", "phoc"),
    ("session", "phoc"), ("session", "server"), ("phoc", "vendor"), ("server", "services"), ("server", "profile"),
    ("updater", "systemd"), ("session", "systemd"), ("server", "systemd"), ("services", "systemd"),
    ("vendor", "kernel"), ("profile", "kernel"), ("systemd", "kernel"), ("kernel", "boot"),
)


class ParseError(Exception):
    pass


def is_trigger(name):
    return Path(name).suffix in TRIGGER_SECTIONS


@dataclass
class DropIn:
    dest: str
    source: str | None  # path under packaging/duo-os, or None when build-image.sh writes it itself
    condition: str | None


@dataclass
class Unit:
    name: str
    path: Path
    keys: dict = field(default_factory=dict)  # (section, key) -> [values]
    dropins: list = field(default_factory=list)

    def get(self, section, key):
        return list(self.keys.get((section, key), []))

    def deps(self, key, section="Unit"):
        return [w for v in self.get(section, key) for w in v.split()]

    def execs(self):
        return [(k, v) for k in EXEC_KEYS for v in self.get("Service", k)]


@dataclass
class Install:
    dest: str
    source: str | None
    mode: str | None
    condition: str | None


@dataclass
class Link:
    target: str  # the unit whose .wants/ holds the link
    unit: str
    points_to: str
    condition: str | None


def parse_unit_text(text, into=None):
    """systemd's unit syntax, as far as these files use it: sections, Key=Value, # and ; comments, continued lines,
    repeated keys appended and an empty assignment resetting a list."""
    keys = {} if into is None else into
    section = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        while line.endswith("\\") and i < len(lines):
            line = line[:-1] + " " + lines[i].strip()
            i += 1
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line or section is None:
            raise ParseError(f"not a unit line: {line!r}")
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v == "":
            keys[(section, k)] = []
        else:
            keys.setdefault((section, k), []).append(v)
    return keys


def _logical_lines(text):
    """Shell lines with backslash continuations joined and quoted strings that span lines kept together."""
    phys = text.splitlines()
    joined = []
    i = 0
    while i < len(phys):
        line = phys[i]
        i += 1
        while line.rstrip().endswith("\\") and i < len(phys):
            line = line.rstrip()[:-1] + " " + phys[i].strip()
            i += 1
        joined.append(line)
    out = []
    buf = None
    for line in joined:
        buf = line if buf is None else buf + "\n" + line
        try:
            tokens = _split(buf)
        except ValueError:
            continue
        out.append((buf, tokens))
        buf = None
    if buf is not None:
        raise ParseError("build-image.sh: a quoted string is not closed")
    return out


def _split(s):
    lex = shlex.shlex(s, posix=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _image_path(token):
    return token[2:] if token.startswith("$r/") else None


def _here_path(token):
    return token[len("$here/"):] if token.startswith("$here/") else None


_WANTS = re.compile(r"^/etc/systemd/system/(" + UNIT_NAME + r")\.(?:wants|requires)/(" + UNIT_NAME + r")$")


class BuildScript:
    """What build-image.sh puts into the image: files (installed or written), their modes and the .wants links."""

    def __init__(self, text):
        self.installs = {}
        self.links = {}
        self.conds = []
        lines = _logical_lines(text)
        self._run(lines, {})

    def _cond(self):
        return " && ".join(self.conds) or None

    def _run(self, lines, subst):
        i = 0
        while i < len(lines):
            raw, tokens = lines[i]
            i += 1
            if subst:
                raw = self._subst(raw, subst)
                tokens = _split(raw)
            if not tokens:
                continue
            head = tokens[0]
            if any(t.startswith("<<") for t in tokens):  # a here-document: its body is data
                delim = next(t for t in tokens if t.startswith("<<"))[2:].lstrip("-")
                self._command(tokens)
                while i < len(lines) and lines[i][0].strip() != delim:
                    i += 1
                i += 1
                continue
            if head == "for" and len(tokens) > 3 and tokens[2] == "in":
                var = tokens[1]
                values = []
                for t in tokens[3:]:
                    if t == "do":
                        break
                    values.append(t.rstrip(";"))
                    if t.endswith(";"):
                        break
                body, depth = [], 1
                while i < len(lines):
                    b_raw, b_tok = lines[i]
                    i += 1
                    if b_tok and b_tok[0] == "for":
                        depth += 1
                    if b_tok == ["done"]:
                        depth -= 1
                        if depth == 0:
                            break
                    body.append((b_raw, b_tok))
                for v in values:
                    self._run(body, {**subst, var: v})
                continue
            if head == "if" and tokens[-1] != "fi":
                self.conds.append(raw.strip())
                continue
            if head == "elif" and self.conds:
                self.conds[-1] = raw.strip()
                continue
            if head == "else" and self.conds:
                self.conds[-1] = "not: " + self.conds[-1]
                continue
            if head == "fi" and self.conds:
                self.conds.pop()
                continue
            self._command(tokens)

    @staticmethod
    def _subst(raw, subst):
        for var, val in subst.items():
            raw = re.sub(r"\$\{" + re.escape(var) + r"\}|\$" + re.escape(var) + r"(?![A-Za-z0-9_])", val, raw)
        return raw

    def _command(self, tokens):
        head = tokens[0]
        cond = self._cond()
        if head == "install" and "-d" not in tokens:
            mode, args, j = None, [], 1
            while j < len(tokens):
                t = tokens[j]
                if t in ("-m", "-o", "-g"):
                    if t == "-m":
                        mode = tokens[j + 1]
                    j += 2
                    continue
                args.append(t)
                j += 1
            if len(args) == 2 and _image_path(args[1]):
                dest = _image_path(args[1])
                self.installs[dest] = Install(dest, _here_path(args[0]), mode, cond)
            return
        if head == "ln":
            args = [t for t in tokens[1:] if not t.startswith("-")]
            if len(args) == 2 and _image_path(args[1]):
                m = _WANTS.match(_image_path(args[1]))
                if m:
                    self.links[(m.group(1), m.group(2))] = Link(m.group(1), m.group(2), args[0], cond)
            return
        if head == "rm" and cond is None:
            for t in tokens[1:]:
                p = _image_path(t)
                if not p:
                    continue
                m = _WANTS.match(p)
                if m:
                    self.links.pop((m.group(1), m.group(2)), None)
                self.installs.pop(p, None)
            return
        if head == "chmod" and len(tokens) == 3 and _image_path(tokens[2]):
            inst = self.installs.get(_image_path(tokens[2]))
            if inst:
                inst.mode = tokens[1]
            return
        for redir in (">", ">>"):
            if redir in tokens:
                k = tokens.index(redir)
                dest = _image_path(tokens[k + 1]) if k + 1 < len(tokens) else None
                if dest:
                    source = next((_here_path(t) for t in tokens[1:k] if _here_path(t)), None)
                    if dest not in self.installs or redir == ">":
                        self.installs[dest] = Install(dest, source, None, cond)
                return


class Graph:
    def __init__(self, root, units, build):
        self.root = Path(root)
        self.units = units
        self.installs = build.installs
        self.links = list(build.links.values())
        self.order = {}  # node -> set of nodes ordered after it
        self.trigger = set()  # (path or timer unit, unit it starts)
        for u in units.values():
            for x in u.deps("After"):
                self._edge(x, u.name)
            for x in u.deps("Before"):
                self._edge(u.name, x)
            if is_trigger(u.name):
                t = self.trigger_unit(u)
                self.trigger.add((u.name, t))
                self._edge(u.name, t)
        for l in self.links:
            u = units.get(l.unit)
            if (l.target.endswith(".target") and u is not None
                    and u.get("Unit", "DefaultDependencies") != ["no"] and l.target not in u.deps("After")):
                self._edge(l.unit, l.target)
        for a, b in INHERITED_ORDER:
            self._edge(a, b)

    @staticmethod
    def trigger_unit(u):
        suffix = Path(u.name).suffix
        named = u.get(TRIGGER_SECTIONS[suffix], "Unit")
        return named[-1] if named else u.name[: -len(suffix)] + ".service"

    def _edge(self, a, b):
        self.order.setdefault(a, set()).add(b)
        self.order.setdefault(b, set())

    def after(self, node):
        seen, todo = set(), list(self.order.get(node, ()))
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(self.order.get(n, ()))
        return seen

    def precedes(self, a, b):
        return b in self.after(a)

    def reduced(self):
        """The ordering edges that no longer path implies (a -> b kept when b is not reachable through another node)."""
        out = set()
        for a, succ in self.order.items():
            for b in succ:
                if not any(b in self.after(c) for c in succ if c != b):
                    out.add((a, b))
        return out

    def pulls(self, dev):
        edges = {}
        for l in self.links:
            if dev or l.condition is None:
                edges.setdefault(l.target, set()).add(l.unit)
        for a, b in INHERITED_PULLS:
            edges.setdefault(a, set()).add(b)
        for u in self.units.values():
            for k in ("Wants", "Requires", "BindsTo", "OnFailure"):
                for x in u.deps(k):
                    edges.setdefault(u.name, set()).add(x)
            if is_trigger(u.name):
                edges.setdefault(u.name, set()).add(self.trigger_unit(u))
        return edges

    def reachable(self, dev=True, roots=(DEFAULT_TARGET,)):
        edges = self.pulls(dev)
        seen, todo = set(), list(roots)
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(edges.get(n, ()))
        return seen

    def levels(self):
        level = {}
        for n in self._topo():
            preds = [p for p, s in self.order.items() if n in s]
            level[n] = 1 + max((level[p] for p in preds), default=-1)
        return level

    def _topo(self):
        indeg = {n: 0 for n in self.order}
        for s in self.order.values():
            for b in s:
                indeg[b] += 1
        ready = sorted(n for n, d in indeg.items() if d == 0)
        out = []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for b in sorted(self.order[n]):
                indeg[b] -= 1
                if indeg[b] == 0:
                    ready.append(b)
                    ready.sort()
        if len(out) != len(indeg):
            raise ParseError("the ordering graph has a cycle")
        return out


def load(root=REPO):
    root = Path(root)
    pkg = root / PKG_REL
    units = {}
    for d in (pkg, pkg / "updater"):
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in UNIT_SUFFIXES:
                if p.name in units:
                    raise ParseError(f"two unit files named {p.name}")
                units[p.name] = Unit(p.name, p, parse_unit_text(p.read_text(encoding="utf-8")))
    build = BuildScript((pkg / "build-image.sh").read_text(encoding="utf-8"))
    for inst in build.installs.values():
        m = re.match(r"^/etc/systemd/system/(" + UNIT_NAME + r")\.d/[^/]+\.conf$", inst.dest)
        if not m or m.group(1) not in units:
            continue
        u = units[m.group(1)]
        u.dropins.append(DropIn(inst.dest, inst.source, inst.condition))
        if inst.source is not None and (pkg / inst.source).is_file():
            parse_unit_text((pkg / inst.source).read_text(encoding="utf-8"), into=u.keys)
    return Graph(root, units, build)


def references(g):
    """Every unit name an OPOS unit or build-image.sh points at: (who, name)."""
    out = []
    for u in g.units.values():
        for k in DEP_KEYS:
            out += [(u.name, x) for x in u.deps(k)]
        for k in INSTALL_KEYS:
            out += [(u.name, x) for x in u.deps(k, "Install")]
        if is_trigger(u.name):
            out.append((u.name, g.trigger_unit(u)))
        for _, v in u.execs():
            out += [(u.name, x) for x in re.findall(r"(?<![\w/.-])" + UNIT_NAME + r"(?![\w.-])", v)]
    out += [("build-image.sh", l.target) for l in g.links]
    return out


def entry_points(g):
    """For each unit, the OPOS-installed programs and the opos Python modules its Exec lines run."""
    out = {}
    for u in g.units.values():
        found = []
        for _, v in u.execs():
            found += OPOS_PATH.findall(v)
            found += PY_MODULE.findall(v)
        out[u.name] = found
    return out


def check_references(g):
    problems = []
    seen = set()
    for who, name in references(g):
        seen.add(name)
        if name not in g.units and name not in INHERITED:
            problems.append(f"{who}: {name} is neither shipped in {PKG_REL} nor listed in INHERITED")
    for name in INHERITED:
        if name in g.units:
            problems.append(f"INHERITED lists {name}, which OPOS ships")
        elif name not in seen:
            problems.append(f"INHERITED lists {name}, which nothing references any more")
    for a, b in INHERITED_ORDER + INHERITED_PULLS:
        for n in (a, b):
            if n not in INHERITED:
                problems.append(f"INHERITED_ORDER/INHERITED_PULLS name {n}, which is not in INHERITED")
    return problems


def check_failure_classes(g):
    problems = []
    for name in sorted(g.units):
        if name not in FAILURE_CLASS:
            problems.append(f"{name}: no entry in FAILURE_CLASS ({' or '.join(CLASSES)})")
    for name, (cls, _) in sorted(FAILURE_CLASS.items()):
        if name not in g.units:
            problems.append(f"FAILURE_CLASS lists {name}, which is not shipped")
        if cls not in CLASSES:
            problems.append(f"FAILURE_CLASS: {name} has class {cls!r}")
    session = g.units.get("opos.service")
    pulled = set()
    if session is not None:
        pulled = {x for k in ("Wants", "Requires", "BindsTo") for x in session.deps(k)}
    for name, (cls, _) in sorted(FAILURE_CLASS.items()):
        if cls != FATAL or name not in g.units or name == "opos.service":
            continue
        if name not in pulled or not g.precedes(name, "opos.service"):
            problems.append(f"{name} is {FATAL} but opos.service does not pull it in and wait for it")
    for name, (cls, _) in sorted(FAILURE_CLASS.items()):
        if cls != FATAL or name not in g.units:
            continue
        for k in ("Requires", "BindsTo", "Requisite"):
            for x in g.units[name].deps(k):
                if FAILURE_CLASS.get(x, (None,))[0] == DEGRADE:
                    problems.append(f"{name} ({FATAL}) has {k}={x}, a {DEGRADE} unit: its failure would take the session down")
    return problems


def check_cycles(g):
    problems = []
    index, low, stack, on, counter = {}, {}, [], set(), [0]
    sys.setrecursionlimit(max(1000, 10 * len(g.order)))

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(g.order[v]):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in g.order[v]:
                problems.append("ordering cycle: " + " -> ".join(sorted(comp)) + " (After=/Before=)")

    for v in sorted(g.order):
        if v not in index:
            strong(v)
    return problems


def check_order(g):
    return [f"{a} is not ordered before {b}" for a, b in BOOT_ORDER if not g.precedes(a, b)]


def check_installed(g):
    problems = []
    pkg = g.root / PKG_REL
    for name, u in sorted(g.units.items()):
        rel = u.path.relative_to(pkg).as_posix()
        inst = g.installs.get(UNIT_DIR + name)
        if inst is None:
            problems.append(f"{name}: build-image.sh does not install it into {UNIT_DIR}")
        elif inst.source != rel:
            problems.append(f"{name}: build-image.sh installs {UNIT_DIR}{name} from {inst.source}, not {rel}")
        elif not _allowed(name, inst.condition):
            problems.append(f"{name}: installed only when {inst.condition}, and BUILD_CONDITIONAL does not list it")
        for d in u.dropins:
            if d.source is not None and not (pkg / d.source).is_file():
                problems.append(f"{name}: drop-in {d.dest} comes from {d.source}, which does not exist")
    for inst in sorted(g.installs.values(), key=lambda i: i.dest):
        if inst.source is not None and not (pkg / inst.source).is_file():
            problems.append(f"build-image.sh installs {inst.dest} from {inst.source}, which does not exist")
    return problems


def check_enabled(g):
    problems = []
    linked = {(l.target, l.unit) for l in g.links}
    for name, u in sorted(g.units.items()):
        for k in INSTALL_KEYS:
            for t in u.deps(k, "Install"):
                if (t, name) not in linked:
                    problems.append(f"{name}: [Install] {k}={t} but build-image.sh links no {t}.wants/{name}")
    for l in sorted(g.links, key=lambda l: (l.target, l.unit)):
        u = g.units.get(l.unit)
        if u is None:
            problems.append(f"build-image.sh links {l.target}.wants/{l.unit}, which OPOS does not ship")
            continue
        if l.points_to != UNIT_DIR + l.unit:
            problems.append(f"build-image.sh links {l.target}.wants/{l.unit} to {l.points_to}")
        if l.target not in u.deps("WantedBy", "Install") + u.deps("RequiredBy", "Install"):
            problems.append(f"build-image.sh links {l.target}.wants/{l.unit}, but its [Install] does not name {l.target}")
    dev = g.reachable(dev=True)
    rel = g.reachable(dev=False)
    for name in sorted(g.units):
        if name not in dev:
            problems.append(f"{name}: nothing starts it (no .wants link, Wants=, Requires=, BindsTo=, OnFailure=, path or "
                            f"timer)")
    dev_roots = {l.unit for l in g.links if l.condition is not None and (l.target, l.unit) not in
                 {(m.target, m.unit) for m in g.links if m.condition is None}}
    conditional = {n for n in dev_roots if n not in rel}
    if set(BUILD_CONDITIONAL) != conditional:
        problems.append(f"units enabled only under a build condition: {sorted(conditional)}, "
                        f"BUILD_CONDITIONAL says {sorted(BUILD_CONDITIONAL)}")
    for name, var in BUILD_CONDITIONAL.items():
        for l in g.links:
            if l.unit == name and (l.condition is None or var not in l.condition):
                problems.append(f"{name}: its link is not under {var}")
    only_dev = (dev - rel) & set(g.units)
    from_dev = g.reachable(dev=True, roots=tuple(BUILD_CONDITIONAL)) & set(g.units)
    if not only_dev <= from_dev:
        problems.append(f"units missing from an image without build inputs, and not in BUILD_CONDITIONAL: "
                        f"{sorted(only_dev - from_dev)}")
    return problems


def check_entry_points(g):
    problems = []
    src = g.root / "src"
    for name, found in sorted(entry_points(g).items()):
        u = g.units[name]
        if not u.get("Service", "ExecStart") and not is_trigger(name):
            problems.append(f"{name}: no ExecStart=")
        for ep in found:
            if ep.startswith("/"):
                inst = g.installs.get(ep)
                if inst is None:
                    problems.append(f"{name}: runs {ep}, which build-image.sh does not install")
                elif not _allowed(name, inst.condition):
                    problems.append(f"{name}: runs {ep}, installed only when {inst.condition}")
                elif inst.mode not in ("0755", "755"):
                    problems.append(f"{name}: runs {ep}, installed with mode {inst.mode}, not 0755")
            else:
                parts = ep.split(".")
                base = src.joinpath(*parts)
                if not (base.with_suffix(".py").is_file() or (base / "__main__.py").is_file()):
                    problems.append(f"{name}: runs python3 -m {ep}, and src/{'/'.join(parts)}.py does not exist")
    return problems


def check_fallback(g):
    problems = []
    u = g.units.get("opos.service")
    if u is None:
        return ["opos.service is not shipped"]
    if "opos-fallback.service" not in u.deps("OnFailure"):
        problems.append("opos.service: OnFailure=opos-fallback.service is missing")
    if "75" not in " ".join(u.get("Service", "RestartPreventExitStatus")).split():
        problems.append("opos.service: RestartPreventExitStatus= does not hold 75, so a kiosk give-up restarts the session")
    if "75" in " ".join(u.get("Service", "SuccessExitStatus")).split():
        problems.append("opos.service: SuccessExitStatus= holds 75, so a give-up would end the unit without a failure")
    if not any("/usr/libexec/opos/opos-session" in v for v in u.get("Service", "ExecStart")):
        problems.append("opos.service: ExecStart= does not run /usr/libexec/opos/opos-session")
    session = g.root / PKG_REL / "opos-session"
    if not session.is_file() or not re.search(r"^\s*exit 75\b", session.read_text(encoding="utf-8"), re.M):
        problems.append("opos-session: no 'exit 75' for the kiosk give-up")
    fb = g.units.get("opos-fallback.service")
    if fb is None or not any("systemctl start phosh.service" in v for _, v in fb.execs()):
        problems.append("opos-fallback.service: does not start phosh.service")
    return problems


def _cls(name):
    return FAILURE_CLASS[name][0] if name in FAILURE_CLASS else "inherited"


def print_sequence(g):
    level = g.levels()
    red = g.reduced()
    rows = sorted(level, key=lambda n: (level[n], n))
    out = [f"{'level':<6} {'unit':<38} {'class':<17} waits for"]
    for n in rows:
        preds = sorted(a for a, b in red if b == n)
        out.append(f"{level[n]:<6} {n:<38} {_cls(n):<17} {', '.join(preds) or '-'}")
    return "\n".join(out) + "\n"


def _id(name):
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def boot_mermaid(g):
    """Solid arrow: waits for (After=/Before=). Dotted: starts (a path or timer unit's trigger, OnFailure=)."""
    out = ["flowchart TD"]
    nodes = sorted(g.order)
    for n in nodes:
        out.append(f'  {_id(n)}["{n}"]')
    lines = []
    for a, b in g.reduced():
        if (a, b) not in g.trigger:
            lines.append(f"  {_id(b)} --> {_id(a)}")
    for p, t in g.trigger:
        lines.append(f"  {_id(p)} -. starts .-> {_id(t)}")
    for u in g.units.values():
        for f in u.deps("OnFailure"):
            lines.append(f"  {_id(u.name)} -. OnFailure .-> {_id(f)}")
    out += sorted(lines)
    out.append("  classDef inherited stroke-dasharray: 4 3")
    out.append("  classDef fatal stroke-width: 3px")
    inh = [_id(n) for n in nodes if n not in g.units]
    fatal = [_id(n) for n in nodes if _cls(n) == FATAL]
    if inh:
        out.append("  class " + ",".join(inh) + " inherited")
    if fatal:
        out.append("  class " + ",".join(fatal) + " fatal")
    return "\n".join(out) + "\n"


def layers_mermaid():
    out = ["flowchart TD"]
    for a, b in LAYER_EDGES:
        out.append(f'  {a}["{LAYER_NODES[a]}"] --> {b}["{LAYER_NODES[b]}"]')
    return "\n".join(out) + "\n"


_BOOT_TABLE_HEAD = "| Step | Unit or program | Class | Waits for | If it fails | Marker |"
_UNIT_IN_CELL = re.compile(r"`(" + UNIT_NAME + r")`")


def check_doc(g, root=REPO):
    root = Path(root)
    doc = root / DOC_REL
    if not doc.is_file():
        return [f"{DOC_REL} does not exist"]
    text = doc.read_text(encoding="utf-8")
    problems = []
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    want = {"--mermaid": boot_mermaid(g), "--layers": layers_mermaid()}
    for flag, body in want.items():
        if body not in blocks:
            problems.append(f"{DOC_REL}: no mermaid block equals bootgraph.py {flag}")
    for b in blocks:
        if b not in want.values():
            problems.append(f"{DOC_REL}: a mermaid block equals neither bootgraph.py --mermaid nor --layers")
    if print_sequence(g) not in re.findall(r"```text\n(.*?)```", text, re.S):
        problems.append(f"{DOC_REL}: no text block equals bootgraph.py --print")
    lines = text.splitlines()
    if _BOOT_TABLE_HEAD not in lines:
        return problems + [f"{DOC_REL}: the boot sequence table header is missing"]
    i = lines.index(_BOOT_TABLE_HEAD) + 2
    named = set()
    while i < len(lines) and lines[i].startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        i += 1
        if len(cells) != 6:
            problems.append(f"{DOC_REL}: boot table row has {len(cells)} cells: {lines[i - 1]}")
            continue
        units = _UNIT_IN_CELL.findall(cells[1])
        waits = _UNIT_IN_CELL.findall(cells[3])
        for u in units:
            named.add(u)
            if u not in g.units and u not in INHERITED:
                problems.append(f"{DOC_REL}: boot table names {u}, which is neither shipped nor inherited")
            elif cells[2] != _cls(u):
                problems.append(f"{DOC_REL}: boot table says {u} is {cells[2]}, FAILURE_CLASS says {_cls(u)}")
            for w in waits:
                if not g.precedes(w, u):
                    problems.append(f"{DOC_REL}: boot table says {u} waits for {w}; the unit files do not order them")
    for name in sorted(set(g.units) - named):
        problems.append(f"{DOC_REL}: boot table has no row for {name}")
    return problems


def all_problems(root=REPO):
    try:
        g = load(root)
    except (OSError, ParseError) as e:
        return [f"cannot read the graph: {e}"]
    out = []
    for check in (check_references, check_failure_classes, check_cycles, check_order, check_installed,
                  check_enabled, check_entry_points, check_fallback):
        out += check(g)
    if not check_cycles(g):
        out += check_doc(g, root)
    else:
        out.append(f"{DOC_REL}: not compared (--print and --mermaid need an ordering without a cycle)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Duo image's service graph, read from the unit files.")
    ap.add_argument("--root", default=str(REPO), help="repository root (default: this checkout)")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--check", action="store_true", help="run every check (the default)")
    what.add_argument("--print", action="store_true", help="the boot order by level")
    what.add_argument("--mermaid", action="store_true", help="the boot graph as Mermaid")
    what.add_argument("--layers", action="store_true", help="the layer diagram as Mermaid")
    a = ap.parse_args(argv)
    if a.layers:
        sys.stdout.write(layers_mermaid())
        return 0
    if a.print or a.mermaid:
        try:
            g = load(a.root)
            sys.stdout.write(print_sequence(g) if a.print else boot_mermaid(g))
        except (OSError, ParseError) as e:
            print(f"bootgraph: {e}", file=sys.stderr)
            return 1
        return 0
    problems = all_problems(a.root)
    for p in problems:
        print(p)
    print("boot graph: OK" if not problems else f"boot graph: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
