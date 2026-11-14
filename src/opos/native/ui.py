# SPDX-License-Identifier: GPL-3.0-or-later
"""The native shell's few widgets and its one stylesheet. Three kinds of thing can be touched, and they look different:
an action (outlined), the main action of a screen (filled), and a row of content (a line of text with a rule under it)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gtk, Pango  # noqa: E402

from opos.native.layout import pane_order  # noqa: E402
from opos.native.logic import RowSet  # noqa: E402

# Every frame is one complete, accurate snapshot and each tap is answered in a single frame. Movement (slides, fades,
# colour fades) is a skin to add later, on top of that, and is off: nothing in the shell animates unless this is turned
# on, and nothing it shows ever depends on an animation ending. Scrolling is not part of that switch (see reading()).
ANIMATE = False
MOVE_MS = 180

CSS = """
window, .pane, .sheet { background: #000; color: #e6e2d9; font-family: sans-serif; font-size: 20px; }
.clock { font-family: serif; font-weight: 700; font-size: 132px; }
.head  { font-family: serif; font-weight: 700; font-size: 40px; }
.big   { font-size: 28px; }
.mid   { font-size: 22px; }
.dim   { opacity: 0.6; }
.strip { font-size: 17px; opacity: 0.7; }
.rule  { border-bottom: 1px solid alpha(#e6e2d9, 0.22); }
button { background: #000; background-image: none; color: #e6e2d9; border: 1px solid alpha(#e6e2d9, 0.55);
         border-radius: 0; box-shadow: none; outline: none; min-height: 68px; padding: 6px 16px; font-size: 24px; }
button:active, button.on { background: #e6e2d9; color: #000; }
button.main { background: #e6e2d9; color: #000; border-color: #e6e2d9; font-weight: 700; min-height: 80px; }
button.main:active { background: #000; color: #e6e2d9; }
button:disabled, label:disabled { opacity: 0.3; }
button.row { border: none; border-bottom: 1px solid alpha(#e6e2d9, 0.22); min-height: 74px; padding: 6px 4px; }
button.row:active { background: alpha(#e6e2d9, 0.18); color: #e6e2d9; }
button.tile { min-height: 116px; font-size: 26px; }
button.key { min-height: 64px; min-width: 0; padding: 0; font-size: 24px; border-color: alpha(#e6e2d9, 0.35); }
button.pad { min-height: 84px; min-width: 0; padding: 0; font-size: 30px; }
button.chip { min-height: 52px; min-width: 0; padding: 2px 8px; font-size: 19px; }
.typed { border-bottom: 2px solid #e6e2d9; padding: 8px 0; font-size: 26px; }
"""


def label(text="", *classes, wrap=True, x=0.0):
    w = Gtk.Label(label=text, xalign=x, wrap=wrap)
    w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # word, then character: a long number or address never pushes the pane wider
    for c in classes:
        w.add_css_class(c)
    return w


def button(text, on_click, *classes):
    b = Gtk.Button(label=text)
    for c in classes:
        b.add_css_class(c)
    b.connect("clicked", lambda *_: on_click())
    return b


def row(title, detail, on_click):
    """One row of content: the title, and a quieter detail under it, both starting at the left edge."""
    b = Gtk.Button()
    b.add_css_class("row")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.append(label(title, "big", wrap=False))
    if detail:
        box.append(label(detail, "strip", wrap=False))
    for child in (box.get_first_child(), box.get_last_child()):
        child.set_ellipsize(Pango.EllipsizeMode.END)
    b.set_child(box)
    b.connect("clicked", lambda *_: on_click())
    return b


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def fill(box, rows):
    """Show (title, detail, on_click) rows in the box. The buttons are rebuilt only when the words change, but every
    tap calls the newest handler for its place, so rows that read the same and mean something else (another note,
    another alarm, another number) never act on the old one."""
    if not hasattr(box, "rowset"):
        box.rowset = RowSet()
    rebuilt = box.rowset.update(rows)
    if not rebuilt:
        return
    clear(box)
    for i, (title, detail, _on_click) in enumerate(rows):
        box.append(row(title, detail, lambda i=i, box=box: box.rowset.click(i)))


def vbox(*children, spacing=10):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for c in children:
        box.append(c)
    return box


def hbox(*children, spacing=10, same=True):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, homogeneous=same)
    for c in children:
        box.append(c)
    return box


def column(*children, bottom=()):
    """A pane: children from the top, a spring, then the `bottom` children, which sit where the thumb rests."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.add_css_class("pane")
    for m in ("top", "bottom", "start", "end"):
        getattr(box, "set_margin_" + m)(28)
    for c in children:
        box.append(c)
    spring = Gtk.Box()
    spring.set_vexpand(True)
    box.append(spring)
    for c in bottom:
        box.append(c)
    return box


def reading(*children):
    """The content view: a pane that scrolls by touch, never sideways, with no scrollbar taking room.

    kinetic_scrolling is always on, whatever ANIMATE says: in GTK 4 it is what turns on the touchscreen drag and pan
    that let a finger scroll at all (off, a list taller than the pane cannot be moved by touch). GTK's own scroll
    inertia comes with it and is kept, because nothing it shows is ever wrong: the scrolled content is already in its
    final state, and the coast only changes which part of it is in view."""
    view = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                              kinetic_scrolling=True, overlay_scrolling=True)
    view.set_child(column(*children))
    return view


def bar(home, main=None):
    """The bottom of the thumb pane: Home, and the screen's main action beside it, twice as wide."""
    grid = Gtk.Grid(column_spacing=10, column_homogeneous=True)
    grid.attach(home, 0, 0, 1, 1)
    if main is not None:
        grid.attach(main, 1, 0, 2, 1)
    return grid


def two_panes(read, act, hinge, hand="right"):
    """The reading pane and the thumb pane, equal widths, the hinge gap between them."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=hinge, homogeneous=True)
    box.read, box.act = read, act
    for name in pane_order(hand):
        box.append(getattr(box, name))
    return box


class Slide(Gtk.Widget):
    """Holds one child and draws it moved up or down by `offset` pixels, for a surface held under the finger.

    This is direct manipulation, not animation: the offset is only ever where the finger is, and it goes back to 0 in
    the frame the finger lifts (the shell never moves it on a timer). Only the drawing moves: nothing is measured,
    allocated or rebuilt when the offset changes. Taps still land where the child sits at rest, which is why the shell
    claims the touch while it follows a drag.
    """

    def __init__(self, child):
        super().__init__(layout_manager=Gtk.BinLayout(), hexpand=True, vexpand=True)
        self.child, self.dy = child, 0.0
        child.set_parent(self)

    def offset(self, dy):
        dy = float(dy)
        if dy != self.dy:
            self.dy = dy
            self.queue_draw()

    def do_snapshot(self, snapshot):
        if self.dy:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(0, self.dy))
            self.snapshot_child(self.child, snapshot)
            snapshot.restore()
        else:
            self.snapshot_child(self.child, snapshot)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None
        super().do_dispose()


LETTERS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
SIGNS = ("1234567890", "-/:;()&@\"", ".,?!'+=")


class Keys(Gtk.Stack):
    """The on-screen keyboard: letters and a numbers-and-signs layer, built once; shift only changes what the keys say.

    Every key is the same width and the rows are staggered like a keyboard. It calls on_key(text) for a character,
    on_key("\\n") for Return and on_key(None) for Delete. No prediction, no network, nothing stored.
    """

    def __init__(self, on_key):
        super().__init__(transition_type=Gtk.StackTransitionType.NONE)
        self.on_key, self.shift, self.letters = on_key, False, []
        self.add_named(self.layer(LETTERS, True), "abc")
        self.add_named(self.layer(SIGNS, False), "123")

    def layer(self, lines, letters):
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)   # 20 half-key columns
        for y, line in enumerate(lines[:2]):
            for i, ch in enumerate(line):
                grid.attach(self.char(ch, letters), (20 - 2 * len(line)) // 2 + 2 * i, y, 2, 1)
        if letters:
            self.shift_key = button("Shift", self.toggle_shift, "key")
            grid.attach(self.shift_key, 0, 2, 3, 1)
        for i, ch in enumerate(lines[2]):
            grid.attach(self.char(ch, letters), 3 + 2 * i, 2, 2, 1)
        grid.attach(button("Delete", lambda: self.on_key(None), "key"), 17, 2, 3, 1)
        grid.attach(button("123" if letters else "abc", lambda: self.set_visible_child_name("123" if letters else "abc"), "key"), 0, 3, 4, 1)
        grid.attach(button("Space", lambda: self.on_key(" "), "key"), 4, 3, 12, 1)
        grid.attach(button("Return", lambda: self.on_key("\n"), "key"), 16, 3, 4, 1)
        return grid

    def char(self, ch, letter):
        b = button(ch, lambda: self.press(b), "key")
        b.ch = ch
        if letter:
            self.letters.append(b)
        return b

    def press(self, b):
        self.on_key(b.get_label())
        if self.shift:
            self.toggle_shift()

    def toggle_shift(self):
        self.shift = not self.shift
        (self.shift_key.add_css_class if self.shift else self.shift_key.remove_css_class)("on")
        for b in self.letters:
            b.set_label(b.ch.upper() if self.shift else b.ch)

    def numbers(self, on=True):
        self.set_visible_child_name("123" if on else "abc")
