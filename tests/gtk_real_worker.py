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


def measure_notes_review(win, description):
    """Opening the gate keeps the note's pane and its reading area in place."""
    notes = win.pages["notes"]
    read, act = rect_of(notes.read, win), rect_of(notes.act, win)
    notes.delete_button.emit("clicked")
    spin(0.15)
    check(notes.actions.get_visible_child_name() == "review", f"{description}: the review pane opens")
    check(
        read is not None and notes.read.get_mapped() and rect_of(notes.read, win) == read,
        f"{description}: the reading pane has the same bounds under review",
    )
    check(rect_of(notes.act, win) == act, f"{description}: the action pane has the same bounds under review")
    check(not notes.keys.get_mapped(), f"{description}: the edit keys are hidden during review")
    for button in (notes.cancel_delete_button, notes.confirm_delete_button):
        hit = rect_of(button, win)
        check(
            hit is not None
            and act is not None
            and button.get_mapped()
            and act[0] <= hit[0]
            and hit[0] + hit[2] <= act[0] + act[2]
            and act[1] <= hit[1]
            and hit[1] + hit[3] <= act[1] + act[3],
            f"{description}: {button.get_label()} stays within the action pane",
        )
    notes.cancel_delete_button.emit("clicked")
    spin(0.15)
    check(notes.actions.get_visible_child_name() == "edit", f"{description}: Cancel restores the editor")


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
    if "notes" in win.pages:
        notes = win.pages["notes"]
        notes.ident, notes.entry.text = "layout-test", "Long note\n" + "A full line that must remain readable.\n" * 28
        notes.text.set_label(notes.entry.text + "_")
        notes.mode()
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
                    description = f"{hand} hand, page {shown}, {mode}"
                    measure_all(win, description)
                    if name == "notes":
                        measure_notes_review(win, description)
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
