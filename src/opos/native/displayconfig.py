# SPDX-License-Identifier: GPL-3.0-or-later
"""The one piece of org.gnome.Mutter.DisplayConfig the phone's lid daemon asks for: the PowerSaveMode property.

Under phosh, Mutter's display service owns this name and the lid daemon blanks the screens by setting PowerSaveMode to
3 when the lid closes (and again every half minute while it stays closed) and to 0 when it opens. Under this shell
nobody owned the name, so the call failed and both panels stayed lit. own() takes the name on the session bus, only if
nobody has it, and hands each value to a blank.Blanker.

It can never block another owner or hang the shell: the request never replaces an owner and never waits in the queue
(DO_NOT_QUEUE), it lets a later owner that asks for the name take it over (ALLOW_REPLACEMENT), and it is asynchronous,
so with no session bus the shell just says once that the screens will not blank this way. Only PowerSaveMode is served.

PowerSaveState is the pure part and needs no toolkit; gi is imported only inside own().
"""
from __future__ import annotations

import sys

IFACE = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"
PROPERTIES = "org.freedesktop.DBus.Properties"
XML = """<node>
  <interface name="org.gnome.Mutter.DisplayConfig">
    <property name="PowerSaveMode" type="i" access="readwrite"/>
  </interface>
</node>"""


def _say(line):
    print("opos displayconfig: " + line, file=sys.stderr, flush=True)


class PowerSaveState:
    """The property's value, backed by a blanker. No toolkit."""
    NAME = "PowerSaveMode"

    def __init__(self, blanker):
        self.blanker = blanker
        self.values = {self.NAME: 0}

    def get(self, name):
        """The property's value, or None for a property this service does not have."""
        return self.values.get(name)

    @classmethod
    def accepts(cls, name, value):
        return name == cls.NAME and isinstance(value, int) and not isinstance(value, bool)

    def set(self, name, value):
        """Take a new value; returns whether it changed. A repeat still reaches the blanker (a re-asserted 3 blanks
        panels lit while the lid was closed, and blanking twice is harmless). Anything not accepted changes nothing."""
        if not self.accepts(name, value):
            return False
        changed = self.values[name] != value
        self.blanker.set_mode(value)
        self.values[name] = value
        return changed

    def properties_changed_args(self):
        """(interface, {name: value}, invalidated names): the body of a PropertiesChanged signal, as plain values."""
        return IFACE, {self.NAME: self.values[self.NAME]}, []


def own(blanker, say=_say):
    """Own IFACE on the session bus, without replacing an owner, and serve PowerSaveMode from it. Returns the owner id,
    or None when it could not even start. A failure is said once and never raised; losing the name, which happens
    later, is said once too."""
    told = []

    def tell(text):
        if not told:
            told.append(text)
            say(text)

    try:
        from gi.repository import Gio, GLib
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        state = PowerSaveState(blanker)

        def get_property(_conn, _sender, _path, _iface, name):
            value = state.get(name)
            return None if value is None else GLib.Variant("i", value)

        def set_property(conn, _sender, _path, _iface, name, variant):
            try:
                value = variant.unpack()
                if not state.accepts(name, value):
                    return False
                if state.set(name, value):
                    iface, changed, gone = state.properties_changed_args()
                    body = GLib.Variant("(sa{sv}as)", (iface, {k: GLib.Variant("i", v) for k, v in changed.items()}, gone))
                    conn.emit_signal(None, PATH, PROPERTIES, "PropertiesChanged", body)
                return True
            except Exception as err:      # a failure here must not take the shell down
                tell("PowerSaveMode could not be set: %r" % (err,))
                return False

        def bus_acquired(conn, _name):
            try:
                conn.register_object(PATH, info.interfaces[0], None, get_property, set_property)
            except Exception as err:
                tell("cannot serve %s: %r" % (PATH, err))

        def name_lost(_conn, name):
            tell("cannot own %s (another program has it, or there is no session bus); the screens will not blank" % name)

        flags = Gio.BusNameOwnerFlags.DO_NOT_QUEUE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT
        return Gio.bus_own_name(Gio.BusType.SESSION, IFACE, flags, bus_acquired, None, name_lost)
    except Exception as err:
        tell("cannot offer %s: %r" % (IFACE, err))
        return None
