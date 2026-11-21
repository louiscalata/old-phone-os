# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs inside a real GTK 4 session (see tests/test_native_gtk_real.py): builds the real Shell, shows every page in every
layout and measures what GTK made of it. Exit codes: 0 all good, 1 a check failed (the failures are printed), 77 no GTK or
no display here. Never run by the normal test discovery: it is a script."""

import os
import sys
import tempfile
import time

import gtk_real_rules as rules  # this script's own folder is on sys.path; the rules import no GTK

os.environ["OPOS_APPS"] = rules.ALL_APPS  # before the shell reads it: every app is a page
os.environ["OPOS_HAND"] = "right"  # not the hand a person saved on this machine
theme_config = tempfile.TemporaryDirectory(prefix="opos-gtk-theme-")
os.environ["XDG_CONFIG_HOME"] = theme_config.name
os.environ.pop("OPOS_THEME", None)
os.environ.setdefault("OPOS_SPAN", rules.SPAN)
# Unset or empty, the shell would ask 127.0.0.1:8765, where a real OPOS server may be running; the address below is
# refused at once, so the poll thread reaches nothing and the run is hermetic. A value the caller set is kept.
if not os.environ.get("OPOS_SERVER_URL"):
    os.environ["OPOS_SERVER_URL"] = rules.NO_SERVER

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk
except (ImportError, ValueError) as err:
    print(f"no GTK 4: {err}", file=sys.stderr)
    sys.exit(rules.NO_GTK)
if not Gtk.init_check():
    print("no display for GTK", file=sys.stderr)
    sys.exit(rules.NO_GTK)

from opos import convert  # noqa: E402
from opos.native import screens  # noqa: E402
from opos.native import shell as shell_module  # noqa: E402
from opos.native.ui import inset_panes, one_column, shift_insets, two_columns  # noqa: E402

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


def measure_notes_review(notes, description):
    """Opening the gate keeps the note's pane and its reading area in place."""
    win = notes.shell
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


def check_home_tiles(home, description):
    tile = home.tiles.get_first_child()
    previous_bottom = 0
    for _app_id, title in home.shell.apps:
        bounds = rect_of(tile, home.tiles) if tile is not None else None
        check(
            tile is not None
            and tile.get_mapped()
            and tile.get_label() == title
            and bounds is not None
            and bounds[1] >= previous_bottom
            and bounds[2] == home.tiles.get_width()
            and bounds[3] >= 48
            and bounds[1] + bounds[3] <= home.tiles.get_height(),
            f"{description}: {title} fills one row with a 48 px target ({bounds!r})",
        )
        if bounds is not None:
            previous_bottom = bounds[1] + bounds[3]
        tile = tile.get_next_sibling() if tile is not None else None
    check(tile is None, f"{description}: only enabled apps have tiles")


def check_settings_hub(hub, label):
    viewport = hub.read.get_child()
    risk = rect_of(hub.risk, viewport)
    check(
        hub.risk.get_mapped()
        and risk is not None
        and risk[1] >= -0.5
        and risk[1] + risk[3] <= viewport.get_height() + 0.5,
        f"{label}: the complete installation and emergency warning fits the reading viewport",
    )
    previous_bottom = 0
    for name, item in hub.groups.items():
        bounds = rect_of(item, hub.act)
        check(
            item.get_mapped()
            and bounds is not None
            and bounds[1] >= previous_bottom - 0.5
            and bounds[1] + bounds[3] <= hub.act.get_height() + 0.5
            and bounds[3] >= 48,
            f"{label}: {name} fits the single action column with a 48 px target ({bounds!r})",
        )
        if bounds is not None:
            previous_bottom = bounds[1] + bounds[3]


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


def measure_theme_switch(win):
    original = win.pages["home"].open
    original.grab_focus()
    spin(0.15)
    focused = win.get_focus()
    first_box = rect_of(original, win)
    first_color = original.get_color().to_string()
    check(win.theme == "dark", "startup uses the dark palette with no saved preference")
    check(win.set_theme("paper", save=False), "the shell switches to paper")
    spin(0.15)
    check(original.get_color().to_string() != first_color, "paper changes the active button color")
    check(first_box == rect_of(original, win), "paper preserves button geometry")
    check(win.get_focus() is focused, "paper preserves the focused widget")
    measure_all(win, "paper, Home, two panes")
    for side in (0, 1):
        for row in win.rows:
            two_columns(row, "right")
            one_column(row, side)
        spin(0.15)
        measure_all(win, f"paper, Home, back mode {side}")
        check(all(row.empty.has_css_class("back-unused") for row in win.rows), "the unused panel stays black")
    for row in win.rows:
        two_columns(row, win.hand)
    original.grab_focus()
    check(win.set_theme("dark", save=False), "the shell switches back to dark")
    spin(0.15)
    check(first_box == rect_of(original, win), "the original layout is restored for the palette comparison")
    check(win.get_focus() is focused, "the original control is focused again")
    restored = original.get_color().to_string()
    check(restored == first_color, f"dark restores the button color ({restored} vs. {first_color})")


def picked_button(win, x, y):
    widget = win.pick(x, y, Gtk.PickFlags.DEFAULT)
    while widget is not None and not isinstance(widget, Gtk.Button):
        widget = widget.get_parent()
    return widget


def pane_footprints(row, win):
    panes = []
    for pane in (row.read, row.act):
        x, y, w, h = rect_of(pane, win)
        start, end = pane.get_margin_start(), pane.get_margin_end()
        top, bottom = pane.get_margin_top(), pane.get_margin_bottom()
        left = end if pane.get_direction() == Gtk.TextDirection.RTL else start
        panes.append((x - left, y - top, w + start + end, h + top + bottom))
    return panes


def measure_shift_surface(win, row, button, description):
    before = rect_of(button, win)
    panes = pane_footprints(row, win)
    empty = getattr(row, "empty", None)
    dark = rect_of(empty, win) if empty is not None and empty.get_mapped() else None
    x, y, width, height = before
    middle = y + height / 2
    check(picked_button(win, x + width / 2, middle) is button, f"{description}: resting button can be picked")
    check(picked_button(win, x + width + 1, middle) is not button, f"{description}: outside the old button")
    margins = inset_panes(win.rows, win.strip)
    shift_insets(margins, 2, 1)
    spin(0.1)
    shifted = rect_of(button, win)
    check(shifted == (x + 2, y + 1, width, height), f"{description}: the button moves by exactly (2, 1)")
    check(picked_button(win, x + width + 1, middle + 1) is button, f"{description}: picks the newly drawn edge")
    check(
        panes == pane_footprints(row, win),
        f"{description}: the pane positions and hinge seam stay put",
    )
    if dark is not None:
        check(
            row.empty.has_css_class("back-unused") and dark == rect_of(row.empty, win),
            f"{description}: the unused dark pane keeps its position",
        )
    shift_insets(margins, 0, 0)
    spin(0.1)
    check(rect_of(button, win) == before, f"{description}: restoring the margins restores the button")


def measure_pixel_shift(win):
    win.show_page("home")
    opening = win.pages["home"].open
    closing = win.rows[1].act.get_last_child().get_child_at(0, 0)
    for hand in ("right", "left"):
        for row in win.rows:
            two_columns(row, win.hand)
        win.set_hand(hand, save=False)
        for side in (None, 0, 1):
            for row in win.rows:
                two_columns(row, hand)
                if side is not None:
                    one_column(row, side)
            for reveal, row, button in ((False, win.rows[0], opening), (True, win.rows[1], closing)):
                win.set_shade(reveal)
                spin(0.15)
                surface = "sheet" if reveal else "page"
                measure_shift_surface(win, row, button, f"{surface}, {hand} hand, back side {side}")
    win.set_shade(False)
    for row in win.rows:
        two_columns(row, win.hand)


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
    page_checks = {
        "home": check_home_tiles,
        "notes": measure_notes_review,
        "settings": check_settings_hub,
        "set_time": check_timezone_picker,
    }
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
                    page_check = page_checks.get(name)
                    if page_check is not None:
                        page_check(win.pages[name], description)
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
    measure_theme_switch(win)
    measure_pixel_shift(win)
    print(f"checked {checked[0]} things, {len(failures)} failed", flush=True)
    app.exit_code = 1 if failures else 0
    app.quit()
    return False


def activate(app):
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
