# SPDX-License-Identifier: GPL-3.0-or-later
"""What the native screens work out for themselves, with no toolkit: typed text, sums, and the lines shown for an
alarm, a timer and a call. Pure functions and two small classes, so they are tested anywhere."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext

OPS = "+-×÷"


class Entry:
    """Text being typed on the on-screen keyboard. key(None) deletes one character; a new line only where allowed."""

    def __init__(self, limit=160, multiline=False, text=""):
        self.limit, self.multiline, self.text = limit, multiline, text

    def key(self, ch):
        """Apply one key. Returns True when the text changed."""
        before = self.text
        if ch is None:
            self.text = self.text[:-1]
        elif ch == "\n" and not self.multiline:
            return False
        elif isinstance(ch, str) and len(self.text.encode("utf-8")) + len(ch.encode("utf-8")) <= self.limit:
            self.text += ch
        return self.text != before


def _number(text):
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError("That is not a sum.") from None


def evaluate(expr):
    """The value of a sum written with + - × ÷, as text. × and ÷ bind first. Raises ValueError with a plain reason."""
    tokens, cur = [], ""
    for ch in expr.replace(" ", ""):
        if ch.isdigit() or ch == ".":
            cur += ch
        elif ch == "-" and cur == "":
            cur = "-"                                # a minus where a number is due belongs to the number
        elif ch in OPS and cur not in ("", "-"):
            tokens += [_number(cur), ch]
            cur = ""
        else:
            raise ValueError("That is not a sum.")
    if cur in ("", "-"):
        raise ValueError("That is not a sum.")
    tokens.append(_number(cur))
    with localcontext() as ctx:
        ctx.prec = 12
        try:
            terms = [tokens[0]]                      # fold × and ÷ first, left to right
            for op, value in zip(tokens[1::2], tokens[2::2]):
                if op == "×":
                    terms[-1] = terms[-1] * value
                elif op == "÷":
                    terms[-1] = terms[-1] / value
                else:
                    terms += [op, value]
            total = terms[0]
            for op, value in zip(terms[1::2], terms[2::2]):
                total = total + value if op == "+" else total - value
        except (DivisionByZero, InvalidOperation):
            raise ValueError("Cannot divide by zero.") from None
        text = format(+total, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class Calc:
    """A four-function calculator: the sum being typed, and a tape of finished sums, newest last."""

    def __init__(self):
        self.expr, self.tape, self.note = "", [], ""

    def press(self, key):
        """One key: a digit, ".", one of + - × ÷, "=", "C" (clear) or "<" (delete)."""
        self.note = ""
        if key == "C":
            self.expr = ""
        elif key == "<":
            self.expr = self.expr[:-1]
        elif key == "=":
            if not self.expr:
                return
            try:
                answer = evaluate(self.expr)
            except ValueError as err:
                self.note = str(err)
                return
            self.tape = (self.tape + ["%s = %s" % (self.expr, answer)])[-50:]
            self.expr = answer
        elif key in OPS:
            if self.expr and self.expr[-1] in OPS and not (key == "-" and self.expr[-1] in "×÷"):
                self.expr = self.expr[:-1] + key       # a second sign replaces the first
            elif self.expr or key == "-":
                self.expr += key
        elif (key.isdigit() or key == ".") and len(self.expr) < 40:
            last = self.expr
            for op in OPS:
                last = last.rsplit(op, 1)[-1]
            if key != "." or "." not in last:
                self.expr += key


def clock_digits(digits):
    """(hour, minute) from up to four typed digits read as HHMM ("730" is 7:30), or None when that is not a time."""
    if not digits.isdigit() or not 3 <= len(digits) <= 4:
        return None
    hour, minute = int(digits[:-2]), int(digits[-2:])
    return (hour, minute) if hour <= 23 and minute <= 59 else None


def digits_shown(digits):
    """The time being typed, as it reads so far: "", "7", "07:3" style without guessing."""
    return digits if len(digits) < 3 else "%s:%s" % (digits[:-2], digits[-2:])


def alarm_line(row):
    """(title, detail) for one alarm from the server's clock snapshot."""
    days = row.get("days") or []
    when = "Every day" if len(days) == 7 else ("Once" if not days else "%d days a week" % len(days))
    label = row.get("label") or ""
    state = "On" if row.get("enabled") else "Off"
    return "%02d:%02d" % (row.get("hour", 0), row.get("minute", 0)), "  ".join(x for x in (when, label, state) if x)


def _when(text):
    made = datetime.fromisoformat(text)
    return made if made.tzinfo else made.replace(tzinfo=timezone.utc)


def timer_left(fires_at, now):
    """"4 min 05 s left" for a running timer, or "" when it is over or unreadable. now is an ISO time, as fires_at is."""
    try:
        left = int((_when(fires_at) - _when(now)).total_seconds())
    except (TypeError, ValueError):
        return ""
    if left <= 0:
        return ""
    return "%d min %02d s left" % divmod(left, 60) if left >= 60 else "%d s left" % left


def call_line(row):
    """(title, detail) for one call-log row: who, then which way, when and how long."""
    who = row.get("name") or row.get("number") or "Unknown"
    try:
        at = _when(row.get("start_time")).astimezone().strftime("%a %H:%M")
    except (TypeError, ValueError):
        at = ""
    secs = row.get("duration")
    took = "" if not isinstance(secs, (int, float)) else ("%d min" % round(secs / 60) if secs >= 60 else "%d s" % secs)
    way = str(row.get("direction") or "").capitalize()
    return who, "  ".join(x for x in (way, at, took, "Practice" if row.get("practice") else "") if x)


# --- keeping the screen true -------------------------------------------------------------------------------------
# Every frame shows one complete, accurate snapshot of what the server last said. These are the rules for that, with
# no toolkit, so they are tested anywhere.

PRACTICE_CALL = "In a practice call"
PRACTICE_MESSAGES = "Practice mode: nothing you write here is sent."
PRACTICE_SENT = "Practice message. Nothing was sent."
CLOCK_UNSET = "Clock not set: alarms will not ring until the time is known."
LINK_LOST = "Connection to OPOS lost. Showing the last state it sent."
LINK_WAITING = "Waiting for OPOS."


class Sequence:
    """Numbers the requests sent to the server, and lets a reply change the screen only if no newer reply has.

    A reply that arrives after a newer one is older news: it must not put the screen back. issue() is called when a
    request is sent; accept(n) when its reply arrives, and says whether that reply may be drawn.

    accept(n, floor=True) is for the reply to a change the owner asked for (a call, a hang-up, a send): when it is
    drawn, every request issued before that moment is refused as well, even one numbered after n. Such a request (a
    poll sent while the change was on its way) may have been answered by the server before it acted, so its state can
    be older than the reply even though its number is newer: it would bring back Call while the call is up.
    """

    def __init__(self):
        self.issued = self.accepted = 0

    def issue(self):
        self.issued += 1
        return self.issued

    def accept(self, number, floor=False):
        if number <= self.accepted:
            return False
        self.accepted = max(number, self.issued) if floor else number
        return True


class Link:
    """Whether the server is answering. A failed poll never empties the screen: the last good snapshot stays, and the
    screen says the connection is lost until a poll succeeds again. Polls come from one thread, so they arrive in order."""

    def __init__(self):
        self.lost, self.have = False, False

    def shown(self):
        """A snapshot from the server was drawn."""
        self.have = True

    def good(self):
        self.lost = False

    def failed(self):
        self.lost = True

    def text(self):
        """The line the screen shows while the connection is lost, or "" while it is fine."""
        if not self.lost:
            return ""
        return LINK_LOST if self.have else LINK_WAITING


def simulated(snap):
    """True while the server's modem is the practice one: calls and messages go nowhere."""
    return bool((snap.get("simulation") or {}).get("modem"))


def call_words(snap):
    """How a call in progress is named: practice calls say so, and a real one is just "In call"."""
    if not snap.get("in_call"):
        return ""
    return PRACTICE_CALL if simulated(snap) else "In call"


def refusal(snap):
    """The server's own words when its last action was refused (a number it will not dial, an empty battery), or "".
    The server words the notice; an error with no notice still says something."""
    status = snap.get("status")
    if not isinstance(status, str) or not status.startswith("error: "):
        return ""
    notice = snap.get("notice")
    return notice if isinstance(notice, str) and notice else "That did not go through."


def sent(snap):
    """True when the server says the message it was just given went out (in practice, was recorded as practice)."""
    return snap.get("status") == "sent"


def messages_line(snap, outcome=""):
    """The one line under the Messages heading: what the last send did, else the standing practice line."""
    if outcome:
        return outcome
    return PRACTICE_MESSAGES if simulated(snap) else ""


def clock_known(*sources):
    """False when any of the server's snapshots says its clock was never set (alarms cannot ring then)."""
    for s in sources:
        if not isinstance(s, dict):
            continue
        if s.get("clock_ok") is False or (s.get("battery") or {}).get("clock_ok") is False:
            return False
    return True


def time_text(known, now):
    """HH:MM from the time tuple, or "--:--" when the clock was never set: a made-up time is not shown."""
    return time.strftime("%H:%M", now) if known else "--:--"


class RowSet:
    """The rows of one list. The buttons are rebuilt only when the words change, but a tap always goes to the newest
    row's handler at that place, so a list with the same words and new ids (or numbers) never calls an old id."""

    def __init__(self):
        self.words, self.clicks = None, []

    def update(self, rows):
        """rows are (title, detail, on_click). Returns True when the buttons must be rebuilt."""
        words = [tuple(r[:2]) for r in rows]
        self.clicks = [r[2] for r in rows]
        changed = words != self.words
        self.words = words
        return changed

    def click(self, index):
        if 0 <= index < len(self.clicks):
            self.clicks[index]()


class Conversation:
    """The Messages screen's idea of who it is talking to, and whose lines it shows.

    The server keeps one open thread, and a snapshot does not say whose. So the lines are shown under a name only after
    the server answered the "open" for that very person, and later snapshots replace them only if they were asked for
    after that answer (seq > mark): an older one may still carry the previous person's thread.
    """

    def __init__(self):
        self.to = self.confirmed = None
        self.lines, self.mark, self.failed = [], 0, False

    def open(self, number):
        self.to, self.confirmed, self.lines, self.failed = number, None, [], False

    def close(self):
        self.to = self.confirmed = None
        self.lines, self.failed = [], False

    def opened(self, number, lines, issued):
        """The server answered "open" for number; issued is the newest request number sent so far. False if the owner
        has moved on to someone else (the answer is then ignored)."""
        if number != self.to:
            return False
        self.confirmed, self.lines, self.mark, self.failed = number, [str(x) for x in lines or []], issued, False
        return True

    def open_failed(self, number):
        if number == self.to and self.confirmed is None:
            self.failed = True

    def snapshot(self, lines, seq):
        """A snapshot (request number seq) arrived: take its thread only when it cannot be an older person's."""
        if self.to is not None and self.confirmed == self.to and seq > self.mark:
            self.lines = [str(x) for x in lines or []]

    def sent(self, lines, issued):
        """A send was answered with the thread as it now is."""
        if self.to is not None and self.confirmed == self.to:
            self.lines, self.mark = [str(x) for x in lines or []], issued

    def body(self):
        if self.to is None:
            return ""
        if self.failed:
            return "That conversation could not be opened."
        if self.confirmed != self.to:
            return "Opening the conversation."
        return "\n".join(self.lines[-40:]) if self.lines else "No messages with this person yet."


class Draft:
    """Text waiting to be sent. A typed draft stays in the box until the server confirms it went out; while a send is
    out it is pending and cannot be sent twice, and a refusal leaves the words where they were."""

    def __init__(self):
        self.pending, self.boxed = None, False

    def start(self, text, boxed):
        """Begin sending text (boxed: it is the typed draft, not a quick reply). False when a send is already out or
        there is nothing to send."""
        if self.pending is not None or not text.strip():
            return False
        self.pending, self.boxed = text, boxed
        return True

    def done(self, ok, typed):
        """The server answered. Returns what the box should hold now: empty only when the typed draft went out and the
        box still holds exactly it; anything else (a refusal, a quick reply, a failure) leaves the box as it was."""
        was, boxed, self.pending, self.boxed = self.pending, self.boxed, None, False
        return "" if ok and boxed and typed == was else typed


class Confirm:
    """Two taps to delete, as the browser asks: the first tap on a thing arms it, the second on the same thing confirms.
    Choosing another thing (or nothing) disarms it, so a tap meant for one row can never delete another."""

    def __init__(self):
        self.armed = None

    def tap(self, key):
        """True when this tap confirms (the second on the same key); False when it only armed it."""
        if key is None:
            self.armed = None
            return False
        if self.armed == key:
            self.armed = None
            return True
        self.armed = key
        return False

    def keep_only(self, key):
        """The thing on screen is now key: a confirmation armed for anything else is dropped."""
        if self.armed != key:
            self.armed = None


class Pending:
    """One request to the server at a time, and its button stays busy until the screen shows a state that includes it.

    start(n) when request n is sent. answered(n, drawn_seq, newest_issued, ok): the reply came back; drawn_seq is the
    request number of the state now drawn. If the reply itself was drawn (or a newer state was, issued after it) the
    request is over. If the reply lost the race to a state that was asked for before the server had acted on it, or the
    reply failed or timed out (ok False: the request may still have reached the server), the request stays pending
    until a state asked for after the reply arrived (seq > newest_issued) is drawn: a stale "not in a call" can then
    never re-enable Call for a call that is already going.
    """

    def __init__(self):
        self.what, self.after = None, None

    def start(self, what):
        if self.what is not None:
            return False
        self.what, self.after = what, None
        return True

    def answered(self, n, drawn_seq, newest_issued, ok):
        if ok and drawn_seq == n:
            self.what = self.after = None
        else:
            # The reply was dropped as older news, or it failed or timed out (the server may still have acted on it):
            # wait for a state asked for after it before the button is offered again.
            self.after = newest_issued

    def seen(self, seq):
        """A state with request number seq was drawn."""
        if self.what is not None and self.after is not None and seq > self.after:
            self.what = self.after = None


class RingGate:
    """Brings the Clock page to the front once for each ringing alarm, not at every poll: the owner can leave it."""

    def __init__(self):
        self.seen = None

    def new(self, ringing):
        ident = ringing.get("id") if isinstance(ringing, dict) else None
        if ident is None:
            self.seen = None
            return False
        first = ident != self.seen
        self.seen = ident
        return first


class Ready:
    """Tells the launcher the shell is usable only when the window is on screen, has drawn a frame, and has finished
    warming up. say() is called once, when the last of the three has happened."""

    NEED = frozenset(("mapped", "warmed", "painted"))

    def __init__(self, say):
        self.say, self.done, self.said = say, set(), False

    def mark(self, what):
        self.done.add(what)
        if not self.said and self.NEED <= self.done:
            self.said = True
            self.say()
        return self.said


def is_snapshot(data):
    """True for a state from the server (it always has a battery); an error body or nothing at all is not one."""
    return isinstance(data, dict) and "error" not in data and "battery" in data


def needs_browser(snap):
    """True when the server is locked with a PIN: the native shell has no PIN screen, so the browser must show it."""
    return isinstance(snap, dict) and snap.get("locked") is True
