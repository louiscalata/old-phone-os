# SPDX-License-Identifier: GPL-3.0-or-later
"""What the native Network screen works out for itself, with no toolkit: the rows of networks, the line under each,
whether a password is needed, the radio sentences, the replies, and the typed password, which is held so it cannot be
shown.

The core (opos.net, through the server's net_* actions) makes every change; nothing here talks to a radio or to the
core, and nothing here writes a file or a log.
"""
from __future__ import annotations

FALLBACK = "That did not go through."
TOO_MANY = "Too many tries in a row. Wait a few seconds, then try again."
NO_ANSWER = ("OPOS has not answered yet. The change may still be going on; the radios are read again to show what "
             "happened.")
SLOW = "OPOS did not answer in time. Try again in a moment."
WIFI_UNKNOWN ="The Wi-Fi state is not known yet. Wait until the radios have been read, then try again."
HIDDEN = "<password hidden>"
NOT_IN_RANGE = "saved, not in range"
OPEN_NOTE = "Open network: anyone nearby can read what you send over it."
NEED_PW = "Type the network's password first, or tap Cancel."
NEED_NAME = "Type the network's name first."
DISCONNECT_MOVED = "The phone is now connected to another network, so nothing was disconnected. Check the list, then try again."
DISCONNECT_UNKNOWN = ("Which network is connected is not known right now, so nothing was disconnected. Wait until the "
                      "radios have been read, then try again.")
DISCONNECT_CLOSED = ("The choice changed while the radios were read again, so nothing was disconnected. Choose the "
                     "network, then try again.")
NAME_CHANGED = "The name changed, so the password typed for the old name was cleared. Type the password again."
KEYS_WAIT = "Keys are not kept while OPOS is busy. Type the password once it answers."
CHANGED = "This button just changed. Check what it says now, then tap again."
LIST_CHANGED = "The list just changed. Check it, then tap the network again."
REREAD = "Reading the radios again to show what the change did..."
LIST_REREAD = "Reading the list again..."     # the only row while a new visit's first list has not come
LIST_WAIT = "The list is being read again. Wait for it, then tap a network."
NOT_SENT = "_not_sent"     # set on a reply put in place of one when the change was refused before anything ran
DONE_STATES = ("disconnected", "off", "blocked", "absent")     # a Wi-Fi state in which no network is connected
STATUS_KEYS = ("wifi", "airplane", "bluetooth", "data")
LIMIT = 64          # the longest Wi-Fi password there is (64 hex digits); opos.net.check_password refuses more
SSID_MAX = 64       # the core refuses a longer name


def _signal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(100, int(value)))


def rows(scan):
    """The networks of a net_scan reply as display rows: connected first, then strongest; one row per name; then the saved
    names not in range. Each row: ssid, signal, secure, known, supported, active, visible, label, security. [] if malformed."""
    if not isinstance(scan, dict):
        return []
    nets, saved = scan.get("networks"), scan.get("saved")
    if nets is None:
        nets = []
    if not isinstance(nets, list) or not isinstance(saved, (list, type(None))):
        return []
    best = {}
    for n in nets:
        if not isinstance(n, dict):
            continue
        ssid = n.get("ssid")
        if not isinstance(ssid, str) or not ssid or len(ssid) > SSID_MAX:
            continue
        row = {"ssid": ssid, "signal": _signal(n.get("signal")), "secure": n.get("secure") is not False,
               "known": n.get("known") is True, "supported": n.get("supported") is not False,
               "active": n.get("active") is True, "visible": True,
               "label": n.get("label") if isinstance(n.get("label"), str) and n.get("label") else None,
               "security": n.get("security") if isinstance(n.get("security"), str) else ""}
        old = best.get(ssid)
        if old is None or (row["active"] and not old["active"]) or \
                (row["active"] == old["active"] and (row["signal"] or 0) > (old["signal"] or 0)):
            if old is not None and old["active"]:
                row["active"] = True
            best[ssid] = row
    out = sorted(best.values(), key=lambda r: (not r["active"], -(r["signal"] or 0), r["ssid"].lower()))
    for name in saved or []:
        if isinstance(name, str) and name and len(name) <= SSID_MAX and name not in best:
            best[name] = {"ssid": name, "signal": None, "secure": True, "known": True, "supported": True, "active": False,
                          "visible": False, "label": NOT_IN_RANGE, "security": ""}
            out.append(best[name])
    return out


def detail(row):
    """The one line under a network's name: the core's label when it sent one, else built from the row."""
    if not isinstance(row, dict):
        return ""
    if isinstance(row.get("label"), str) and row["label"]:
        return row["label"]
    if row.get("visible") is False:
        return NOT_IN_RANGE
    how = ("connected" if row.get("active") else "saved" if row.get("known") else
           "not supported yet" if row.get("supported") is False else
           "open network" if row.get("secure") is False else "needs a password")
    sig = _signal(row.get("signal"))
    return how + ("" if sig is None else ", signal %d%%" % sig)


def needs_password(row, typed_hidden=False):
    """True when joining must send a typed password: a secured network not saved on the phone. For a network whose name
    was typed (hidden), True unless the owner chose an open network (row["secure"] is False)."""
    if not isinstance(row, dict):
        return True
    if typed_hidden:
        return row.get("secure") is not False
    return row.get("secure") is not False and row.get("known") is not True


def can_join(row):
    """False for networks the core cannot join (company or school 802.1X, old WEP)."""
    return isinstance(row, dict) and row.get("supported") is not False


def why_not(row):
    """Why can_join(row) is False, in a sentence; "" when it can be joined."""
    if can_join(row):
        return ""
    if not isinstance(row, dict):
        return "This kind of network is not supported yet."
    words = "%s %s" % (row.get("security") or "", row.get("label") or "")
    if "802.1X" in words or "company" in words:
        return "Company and school networks are not supported yet."
    if "WEP" in words:
        return "Old WEP security is not supported."
    return "This kind of network is not supported yet."


def choice(row):
    """What the thumb pane offers for a chosen row: idle, connected, unsupported, password, known or open."""
    if not isinstance(row, dict):
        return "idle"
    if row.get("active"):
        return "connected"
    if not can_join(row):
        return "unsupported"
    if needs_password(row):
        return "password"
    return "known" if row.get("known") else "open"


def mask(text):
    """A bullet for each character typed; never the characters."""
    try:
        return "•" * len(text)
    except TypeError:
        return ""


class Secret:
    """A password being typed. reveal() is the only way to read it; printing, formatting or logging it shows HIDDEN, and it
    cannot be copied, pickled or compared. It is never written anywhere by this class.

    It belongs to one network name: each key says which name it is typed for, a key for another name first empties what
    was typed for the earlier one, and is_for(name) is True only for the name every key it holds was typed for. So what
    was typed for one name can never be sent with another."""

    __slots__ = ("__text", "__name")

    def __init__(self):
        self.__text, self.__name = "", None

    def key(self, ch, name=None):
        """One key from the keyboard, typed for the network `name`: a character, or None to delete. Return is not part of
        a password. Keys typed for an earlier, different name are emptied first. True if changed."""
        other = bool(self.__text) and name != self.__name
        if other:
            self.__text = ""
        self.__name = name
        if ch is None:
            return self.delete() or other
        if not isinstance(ch, str) or not ch or "\n" in ch or len(self.__text) + len(ch) > LIMIT:
            return other
        self.__text += ch
        return True

    def is_for(self, name):
        """True when something is typed and every key of it was typed for the network `name`."""
        return bool(self.__text) and isinstance(name, str) and bool(name) and name == self.__name

    def delete(self):
        if not self.__text:
            return False
        self.__text = self.__text[:-1]
        return True

    def clear(self):
        self.__text, self.__name = "", None

    def reveal(self):
        return self.__text

    def __len__(self):
        return len(self.__text)

    def __repr__(self):
        return HIDDEN

    __str__ = __repr__

    def __format__(self, _spec):
        return HIDDEN

    def __eq__(self, _other):
        return NotImplemented   # no comparison can be used to guess it

    __hash__ = object.__hash__

    def __reduce_ex__(self, _protocol):
        raise TypeError("a typed password is not copied or saved")

    def __copy__(self):
        raise TypeError("a typed password is not copied or saved")

    def __deepcopy__(self, _memo):
        raise TypeError("a typed password is not copied or saved")


def scrub(result, secret):
    """The reply with every top-level text that contains the password sent replaced by HIDDEN. The core never echoes a
    password (opos.net.explain removes it), so this changes nothing in use; it keeps a future core from showing one."""
    if not isinstance(result, dict) or not isinstance(secret, str) or not secret:
        return result
    return {k: (HIDDEN if isinstance(v, str) and secret in v else v) for k, v in result.items()}


def status_lines(status):
    """Plain sentences from a net_status reply: Wi-Fi, airplane mode, Bluetooth, mobile data (each entry's summary)."""
    if not isinstance(status, dict):
        return []
    out = []
    for key in STATUS_KEYS:
        entry = status.get(key)
        summary = entry.get("summary") if isinstance(entry, dict) else None
        if isinstance(summary, str) and summary.strip():
            out.append(summary.strip())
    if not out and isinstance(status.get("error"), str) and status["error"].strip():
        out.append(status["error"].strip())
    return out


def wifi_on(status):
    """True or False from a net_status reply's Wi-Fi entry, None when it is not known."""
    w = status.get("wifi") if isinstance(status, dict) else None
    on = w.get("enabled") if isinstance(w, dict) else None
    return on if isinstance(on, bool) else None


def connected_to(status):
    """(known, ssid) from a net_status reply: (True, name) when it says the Wi-Fi is connected to that network, (True,
    None) when it says no network is connected, (False, None) when it does not say (unread, failed, connecting, or
    connected with no name)."""
    w = status.get("wifi") if isinstance(status, dict) else None
    if not isinstance(w, dict):
        return False, None
    if w.get("enabled") is False or w.get("state") in DONE_STATES:
        return True, None
    ssid = w.get("ssid")
    if w.get("state") == "connected" and isinstance(ssid, str) and ssid:
        return True, ssid
    return False, None


def checking(ssid):
    """What the screen says while the read asked by the confirming Disconnect tap is waiting. It names the network."""
    return "Checking that %s is still the connected network..." % ssid


def disconnecting(ssid):
    """What the screen says once that read named the same network and the disconnect has been sent."""
    return "Disconnecting from %s..." % ssid


def friendly(result):
    """(ok, sentence) from a net_join, net_forget or net_wifi reply."""
    if not isinstance(result, dict):
        return False, FALLBACK
    ok = result.get("ok") is True
    for key in (("message", "error") if ok else ("error", "message")):
        text = result.get(key)
        if isinstance(text, str) and text.strip():
            return ok, text.strip()
    return ok, "Done." if ok else FALLBACK


def refused(code):
    """The sentence for an HTTP error from the core: its bodies are short codes, not sentences."""
    return TOO_MANY if code == 429 else FALLBACK


def refusal(code):
    """The reply put in place of an HTTP error from the core. A 4xx is the server refusing the request before it ran
    anything (rate, token, origin, shape), so it is marked NOT_SENT; a 5xx may have come after the change started."""
    got = {"ok": False, "error": refused(code)}
    if isinstance(code, int) and 400 <= code < 500:
        got[NOT_SENT] = True
    return got


def nothing_ran(got):
    """True only for a reply marked NOT_SENT: the change was refused before anything ran, so the radios are as they were.
    Every other answer to a change (confirmed, failed, timed out on either side) may have changed them."""
    return isinstance(got, dict) and got.get(NOT_SENT) is True


def timed_out(err):
    """True when a request to the core ended because no answer came in time (not because it was refused)."""
    return isinstance(err, TimeoutError) or isinstance(getattr(err, "reason", None), TimeoutError)


def failed(err, change):
    """The reply put in place of an exception from the core. A change that timed out may still be going on, so it is
    not called failed; any other error says only that it did not go through."""
    if timed_out(err):
        return {"ok": False, "error": NO_ANSWER if change else SLOW}
    got = {"ok": False, "error": FALLBACK}
    if isinstance(getattr(err, "reason", None), ConnectionRefusedError) or isinstance(err, ConnectionRefusedError):
        got[NOT_SENT] = True    # nothing reached the server
    return got
