# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs inside a real GTK 4 session (see tests/test_native_gtk_real.py): builds the real Shell, shows every page in every
layout and measures what GTK made of it. Exit codes: 0 all good, 1 a check failed (the failures are printed), 77 no GTK or
no display here. Never run by the normal test discovery: it is a script."""

import os
import sys
import time

import gtk_real_rules as rules  # this script's own folder is on sys.path; the rules import no GTK

os.environ["OPOS_APPS"] = rules.ALL_APPS  # before the shell reads it: every app is a page
os.environ["OPOS_HAND"] = "right"  # not the hand a person saved on this machine
os.environ.setdefault("OPOS_SPAN", rules.SPAN)
# Unset or empty, the shell would ask 127.0.0.1:8765, where a real OPOS server may be running; the address below is
# refused at once, so the poll thread reaches nothing and the run is hermetic. A value the caller set is kept.
if not os.environ.get("OPOS_SERVER_URL"):
    os.environ["OPOS_SERVER_URL"] = rules.NO_SERVER

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as err:
    print(f"no GTK 4: {err}", file=sys.stderr)
    sys.exit(rules.NO_GTK)
if not Gtk.init_check():
    print("no display for GTK", file=sys.stderr)
    sys.exit(rules.NO_GTK)

from opos import convert
from opos.native import screens
from opos.native import shell as shell_module
from opos.native.ui import CSS, one_column, two_columns

W, H = rules.W, rules.H
failures, checked = [], [0]


def spin(seconds):
    ctx, end = GLib.MainContext.default(), time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        time.sleep(0.003)


def check(ok, what):
    checked[0] += 1
    if not ok:
        failures.append(what)
        print("FAIL: " + what, flush=True)


def rect_of(widget, win):
    ok, rect = widget.compute_bounds(win)
    return (rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()) if ok else None


def measure_all(win, label):
    """One layout: every row at most H tall at its minimum, and both panes of the page row fully inside the window."""
    for r, row in enumerate(win.rows):
        minimum = row.measure(Gtk.Orientation.VERTICAL, W)[0]
        problem = rules.row_problem(label, r, minimum)
        check(problem is None, problem or "")
    row = win.rows[0]
    for part in ("read", "act"):
        widget = getattr(row, part)
        problems = rules.pane_problems(label, part, rect_of(widget, win), widget.get_mapped())
        check(not problems, "; ".join(problems))


def fill_converter(win):
    """The Converter reads its measures from the server; with no server it would draw only "Measure: not read yet" and
    its real size would go unmeasured. The server's own read (opos.convert) is handed to the page, so the buttons have
    their real words, and a long amount is typed."""
    page = win.pages["convert"]
    page.got(convert.snapshot())
    for key in "-1234567.891":
        page.key(key)
    check(page.spec() is not None, "the Converter page has the server's measures")


def check_timezone_picker(page, label):
    """A two-line city near the bottom of a long list must move wholly into the viewport."""
    zone = "America/City_Number_140"
    zones = ["America/Argentina/Buenos_Aires", "Europe/Paris", "UTC"]
    zones.extend(f"America/City_Number_{n:03d}" for n in range(160))
    page.active = True
    page.got({"zones": zones, "saved": "Europe/Paris", "source": "system"})
    page.open_region("America")
    spin(0.15)

    viewport = page.read.get_child()
    content = page.cities.get_parent()
    adjustment = page.read.get_vadjustment()
    city_rows = {}
    row = page.cities.get_first_child()
    for _, name in page.cities.rowset.words:
        city_rows[name] = row
        row = row.get_next_sibling()

    row = city_rows[zone]
    position = rect_of(row, content)
    if position is None or adjustment.get_page_size() <= 0:
        check(False, f"{label}: the late city row and scroll viewport are allocated")
        return
    adjustment.set_value(position[1] + content.get_margin_top() - adjustment.get_page_size() + 18)
    spin(0.15)
    partial = rect_of(row, viewport)
    check(
        partial is not None and partial[1] < viewport.get_height() < partial[1] + partial[3],
        f"{label}: the late city starts partly visible at the lower edge",
    )
    row.emit("clicked")
    spin(0.2)

    for selected in (zone, "America/Argentina/Buenos_Aires"):
        row = city_rows[selected]
        if selected != zone:
            page.choose(selected)
            spin(0.2)
        check(page.chosen == selected and row.has_css_class("on"), f"{label}: {selected} is chosen")
        title, detail = row.get_child().get_first_child(), row.get_child().get_last_child()
        for widget in (row, title, detail):
            rect = rect_of(widget, viewport)
            check(
                widget.get_mapped()
                and rect is not None
                and rect[1] >= -0.5
                and rect[1] + rect[3] <= viewport.get_height() + 0.5,
                f"{label}: {selected} row and both lines are visible (bounds {rect!r})",
            )
        before = adjustment.get_value()
        page.choose(selected)
        spin(0.15)
        check(abs(adjustment.get_value() - before) < 0.5, f"{label}: reselecting a visible city keeps its position")
    page.leave()


def variants(win, name):
    """What to show of a page: once, or for the Converter once for each measure the server offers."""
    if name == "convert" and win.pages[name].kinds:
        return list(win.pages[name].kinds)
    return [None]


def run(app):
    win = app.win
    deadline = time.monotonic() + 20
    while win.warming and time.monotonic() < deadline:
        spin(0.1)
    check(not win.warming, "the shell finished warming up")
    spin(0.5)
    check(
        (win.get_width(), win.get_height()) == (W, H),
        f"the window is {W}x{H} (got {win.get_width()}x{win.get_height()})",
    )
    pages = list(win.pages)
    print("pages: " + ", ".join(pages), flush=True)
    missing = sorted({c.name for c in screens.PAGES} - set(pages))
    check(not missing, f"every page is built, none left out (missing: {', '.join(missing)})")
    if "convert" in win.pages:
        fill_converter(win)
    for hand in ("right", "left"):
        win.set_hand(hand, save=False)
        for name in pages:
            win.show_page(name)
            for spec in variants(win, name):
                if spec is not None:
                    win.pages[name].use(spec)  # each measure has its own words on the Converter's buttons
                    win.pages[name].changed()
                spin(0.15)
                for mode in ("two", "screen0", "screen1", "two"):
                    for row in win.rows:
                        two_columns(row, hand)
                        if mode != "two":
                            one_column(row, int(mode[-1]))
                    spin(0.15)
                    shown = name if spec is None else f"{name} ({spec['id']})"
                    label = f"{hand} hand, page {shown}, {mode}"
                    measure_all(win, label)
                    if name == "set_time":
                        check_timezone_picker(win.pages[name], label)
    # the battery sheet is the second row with the same panes; it is measured in measure_all
    for row in win.rows:
        two_columns(row, "right")
    win.set_hand("right", save=False)
    spin(0.2)
    # a tap in the middle of Home's Open button, in the thumb pane, lands on that button
    win.show_page("home")
    spin(0.3)
    box = rect_of(win.pages["home"].open, win)
    hit = win.touched(box[0] + box[2] / 2, box[1] + box[3] / 2) if box else None
    check(
        box is not None and hit is not None and hit.startswith("Open"),
        f"a tap on the Open button lands on it (got {hit!r})",
    )
    print(f"checked {checked[0]} things, {len(failures)} failed", flush=True)
    app.exit_code = 1 if failures else 0
    app.quit()
    return False


def activate(app):
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    app.win = shell_module.Shell(app, False)
    app.win.fullscreen()
    app.win.present()
    GLib.timeout_add(300, app.win.warm)
    GLib.timeout_add(500, run, app)


app = Gtk.Application(application_id="org.opos.shelltest", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.exit_code = 2
app.connect("activate", activate)
app.run([])
sys.exit(app.exit_code)
