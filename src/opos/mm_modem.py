# SPDX-License-Identifier: GPL-3.0-or-later
"""ModemManager modem foundation."""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from typing import Any, Protocol

MM = "org.freedesktop.ModemManager1"
MM_PATH = "/org/freedesktop/ModemManager1"
OM = "org.freedesktop.DBus.ObjectManager"
VOICE = "org.freedesktop.ModemManager1.Modem.Voice"
MESSAGING = "org.freedesktop.ModemManager1.Modem.Messaging"
CALL = "org.freedesktop.ModemManager1.Call"
SMS = "org.freedesktop.ModemManager1.Sms"


class Bus(Protocol):
    """D-Bus call interface used by modem helpers."""

    def call(self, bus_name: str, path: str, interface: str, method: str, *args: Any) -> Any:
        ...

    def get(self, bus_name: str, path: str, interface: str, prop: str) -> Any:
        ...


_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _parse_string(s: str, i: int) -> tuple[str, int]:
    q = s[i]
    i += 1
    out: list[str] = []
    while i < len(s):
        c = s[i]
        if c == q:
            return "".join(out), i + 1
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(s):
            i += 1
            break
        e = s[i + 1]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
            continue
        if e == "u" or e == "U":
            n = 4 if e == "u" else 8
            h = s[i + 2 : i + 2 + n]
            if len(h) == n and all(d in "0123456789abcdefABCDEF" for d in h):
                v = int(h, 16)
                if v <= 0x10FFFF and not 0xD800 <= v <= 0xDFFF:
                    out.append(chr(v))
                    i += 2 + n
                    continue
            out.append(e)
            i += 2
            continue
        out.append(e)
        i += 2
    return "".join(out), len(s)


def _parse_variant(s: str, i: int) -> tuple[Any, int]:
    inner = i + 1
    value, nxt = _parse_value(s, inner)  # type: ignore[name-defined]
    if nxt <= inner:
        raise ValueError("no progress in variant")
    nxt = _skip_ws(s, nxt)
    if nxt >= len(s) or s[nxt] != ">":
        raise ValueError("expected '>' in variant")
    return value, nxt + 1


def _parse_array(s: str, i: int) -> tuple[list[Any], int]:
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "]":
        return [], i + 1
    items: list[Any] = []
    while True:
        value, nxt = _parse_value(s, i)  # type: ignore[name-defined]
        if nxt <= i:
            raise ValueError("no progress in array")
        items.append(value)
        i = _skip_ws(s, nxt)
        if i < len(s) and s[i] == ",":
            i = _skip_ws(s, i + 1)
            continue
        if i < len(s) and s[i] == "]":
            return items, i + 1
        raise ValueError("expected ',' or ']' in array")


def _parse_dict(s: str, i: int) -> tuple[dict[Any, Any], int]:
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "}":
        return {}, i + 1
    result: dict[Any, Any] = {}
    while True:
        key, nxt = _parse_value(s, i)  # type: ignore[name-defined]
        if nxt <= i:
            raise ValueError("no progress in dict key")
        i = _skip_ws(s, nxt)
        if i < len(s) and s[i] == ":":
            i = _skip_ws(s, i + 1)
        value, nxt = _parse_value(s, i)  # type: ignore[name-defined]
        if nxt <= i:
            raise ValueError("no progress in dict value")
        if key is not None:
            result[key] = value
        i = _skip_ws(s, nxt)
        if i < len(s) and s[i] == ",":
            i = _skip_ws(s, i + 1)
            continue
        if i < len(s) and s[i] == "}":
            return result, i + 1
        raise ValueError("expected ',' or '}' in dict")


def _parse_tuple(s: str, i: int) -> tuple[tuple[Any, ...], int]:
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == ")":
        return (), i + 1
    items: list[Any] = []
    while True:
        value, nxt = _parse_value(s, i)  # type: ignore[name-defined]
        if nxt <= i:
            raise ValueError("no progress in tuple")
        items.append(value)
        i = _skip_ws(s, nxt)
        if i < len(s) and s[i] == ",":
            i = _skip_ws(s, i + 1)
            if i < len(s) and s[i] == ")":
                return tuple(items), i + 1
            continue
        if i < len(s) and s[i] == ")":
            return tuple(items), i + 1
        raise ValueError("expected ',' or ')' in tuple")


def _parse_value(s: str, i: int) -> tuple[Any, int]:
    i = _skip_ws(s, i)
    if i >= len(s):
        return None, i
    c = s[i]
    if c == "(":
        return _parse_tuple(s, i)
    if c == "[":
        return _parse_array(s, i)
    if c == "{":
        return _parse_dict(s, i)
    if c == "<":
        return _parse_variant(s, i)
    if c == "'" or c == '"':
        return _parse_string(s, i)
    if c == "@":
        j = _skip_ws(s, i + 1)
        k = j
        while k < len(s) and not s[k].isspace():
            k += 1
        if k <= j:
            raise ValueError("no progress in annotation")
        k = _skip_ws(s, k)
        return _parse_value(s, k)
    if c.isdigit() or (c == "-" and i + 1 < len(s) and s[i + 1].isdigit()):
        j = i
        if s[j] == "-":
            j += 1
        if s[j : j + 2] == "0x" or s[j : j + 2] == "0X":
            k = j + 2
            while k < len(s) and s[k] in "0123456789abcdefABCDEF":
                k += 1
            has_int = k > j + 2
            is_hex_float = False
            if k < len(s) and s[k] == ".":
                m = k + 1
                while m < len(s) and s[m] in "0123456789abcdefABCDEF":
                    m += 1
                if not has_int and m == k + 1:
                    raise ValueError("bad numeric token")
                k = m
                is_hex_float = True
            elif not has_int:
                raise ValueError("bad numeric token")
            if k < len(s) and (s[k] == "p" or s[k] == "P"):
                m = k + 1
                if m < len(s) and (s[m] == "+" or s[m] == "-"):
                    m += 1
                e = m
                while e < len(s) and s[e].isdigit():
                    e += 1
                if e == m:
                    raise ValueError("bad numeric token")
                k = e
                is_hex_float = True
            if k < len(s) and (s[k].isalnum() or s[k] == "_" or s[k] == "."):
                raise ValueError("bad numeric token")
            token = s[i:k]
            if is_hex_float:
                try:
                    value = float.fromhex(token)
                except OverflowError as exc:
                    raise ValueError("bad numeric token") from exc
                return value, k
            return int(token, 16), k
        k = j
        while k < len(s) and s[k].isdigit():
            k += 1
        is_float = False
        if k < len(s) and s[k] == ".":
            is_float = True
            k += 1
            while k < len(s) and s[k].isdigit():
                k += 1
        if k < len(s) and (s[k] == "e" or s[k] == "E"):
            is_float = True
            m = k + 1
            if m < len(s) and (s[m] == "+" or s[m] == "-"):
                m += 1
            if m >= len(s) or not s[m].isdigit():
                raise ValueError("bad numeric token")
            m += 1
            while m < len(s) and s[m].isdigit():
                m += 1
            k = m
        if k < len(s) and (s[k].isalpha() or s[k] == "_"):
            raise ValueError("bad numeric token")
        token = s[i:k]
        if is_float:
            return float(token), k
        magnitude = token[1:] if token[:1] == "-" else token
        if len(magnitude) > 1 and magnitude[:1] == "0":
            return int(token, 8), k
        return int(token), k
    if c.isalpha():
        j = i
        while j < len(s) and (s[j].isalnum() or s[j] == "_" or s[j] == "."):
            j += 1
        word = s[i:j]
        k = _skip_ws(s, j)
        low = word.lower()
        if low == "true":
            return True, k
        if low == "false":
            return False, k
        value, nxt = _parse_value(s, k)
        if value is None and nxt <= k:
            raise ValueError("no progress after type name")
        return value, nxt
    return None, i


def parse_gvariant(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    value, nxt = _parse_value(text, 0)
    if _skip_ws(text, nxt) != len(text):
        raise ValueError("trailing data")
    return value


def to_gvariant(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if -2 ** 31 <= value <= 2 ** 31 - 1:
            return str(value)
        if -2 ** 63 <= value <= 2 ** 63 - 1:
            return "int64 " + str(value)
        if 2 ** 63 <= value <= 2 ** 64 - 1:
            return "uint64 " + str(value)
        raise ValueError("integer out of GVariant range: " + str(value))
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(value, dict):
        if not value:
            return "@a{sv} {}"
        parts = [to_gvariant(k) + ": <" + to_gvariant(v) + ">" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    raise TypeError("Unsupported GVariant type: " + type(value).__name__)


class GdbusBus:
    def call(self, bus_name: str, path: str, interface: str, method: str, *args: Any) -> Any:
        parse = globals().get("parse_gvariant")
        if parse is None:
            raise OSError("GVariant decoder unavailable")
        argv = [
            "gdbus",
            "call",
            "--system",
            "--dest",
            bus_name,
            "--object-path",
            path,
            "--method",
            interface + "." + method,
        ]
        for arg in args:
            argv.append(to_gvariant(arg))
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        except subprocess.TimeoutExpired as exc:
            raise OSError("gdbus timed out") from exc
        if result.returncode != 0:
            raise OSError(result.stderr.strip())
        value = parse(result.stdout.strip())
        if isinstance(value, tuple) and len(value) == 1:
            return value[0]
        return value

    def get(self, bus_name: str, path: str, interface: str, prop: str) -> Any:
        parse = globals().get("parse_gvariant")
        if parse is None:
            raise OSError("GVariant decoder unavailable")
        argv = [
            "gdbus",
            "call",
            "--system",
            "--dest",
            bus_name,
            "--object-path",
            path,
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            to_gvariant(interface),
            to_gvariant(prop),
        ]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        except subprocess.TimeoutExpired as exc:
            raise OSError("gdbus timed out") from exc
        if result.returncode != 0:
            raise OSError(result.stderr.strip())
        value = parse(result.stdout.strip())
        if isinstance(value, tuple) and len(value) == 1:
            return value[0]
        return value


def find_modem(bus: Bus) -> str:
    objects = bus.call(MM, MM_PATH, OM, "GetManagedObjects")
    for path in sorted(objects):
        if MM + ".Modem" in objects[path]:
            return path
    raise OSError("no modem")


class MMModem:
    def __init__(self, bus: Bus, modem_path: str | None = None) -> None:
        self.bus = bus
        self._modem_path = modem_path
        self._explicit = modem_path is not None
        self.call_path = None
        self._delivered: dict[str, Any] = {}

    def _modem(self) -> str:
        if self._modem_path is None:
            try:
                self._modem_path = find_modem(self.bus)
            except Exception as exc:
                raise OSError(str(exc)) from exc
        return self._modem_path

    def _bus_call(self, interface: str, method: str, *args: Any) -> Any:
        try:
            return self.bus.call(MM, self._modem(), interface, method, *args)
        except OSError:
            if not self._explicit:
                self._modem_path = None
            raise

    def _bus_get(self, interface: str, prop: str) -> Any:
        try:
            return self.bus.get(MM, self._modem(), interface, prop)
        except OSError:
            if not self._explicit:
                self._modem_path = None
            raise

    def dial(self, number: str) -> str:
        if self.call_path is not None:
            raise OSError("already in a call")
        path = self._bus_call(VOICE, "CreateCall", {"number": number})
        self.call_path = path
        try:
            self.bus.call(MM, path, CALL, "Start")
        except OSError as exc:
            try:
                self.bus.call(MM, self._modem(), VOICE, "DeleteCall", path)
            except OSError:
                pass
            self.call_path = None
            raise OSError(str(exc)) from exc
        return path

    def hang_up(self) -> None:
        if not self.call_path:
            return
        modem = self._modem()
        hangup_error: OSError | None = None
        try:
            self.bus.call(MM, self.call_path, CALL, "Hangup")
        except OSError as exc:
            hangup_error = exc
        delete_error: OSError | None = None
        try:
            self.bus.call(MM, modem, VOICE, "DeleteCall", self.call_path)
        except OSError as exc:
            delete_error = exc
        if hangup_error is None or delete_error is None:
            self.call_path = None
        if delete_error is not None:
            raise OSError(str(delete_error)) from delete_error
        if hangup_error is not None:
            raise OSError("hangup failed") from hangup_error

    def send_sms(self, number: str, text: str) -> None:
        modem = self._modem()
        try:
            path = self._bus_call(MESSAGING, "Create", {"number": number, "text": text})
        except Exception as exc:
            raise OSError(str(exc)) from exc
        try:
            self.bus.call(MM, path, SMS, "Send")
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.bus.call(MM, modem, MESSAGING, "Delete", path)
            raise OSError(str(exc)) from exc
        try:
            self.bus.call(MM, modem, MESSAGING, "Delete", path)
        except OSError:
            pass

    def poll_incoming(self, essentials: Any) -> int:
        paths = self._bus_call(MESSAGING, "List")
        for path in list(self._delivered):
            if path not in paths:
                del self._delivered[path]
        delivered = 0
        for path in sorted(paths):
            try:
                number = self.bus.get(MM, path, SMS, "Number")
                text = self.bus.get(MM, path, SMS, "Text")
                pdu_type = self.bus.get(MM, path, SMS, "PduType")
                state = self.bus.get(MM, path, SMS, "State")
            except Exception:
                continue
            identity = (number, text)
            if path in self._delivered:
                if self._delivered[path] != identity:
                    del self._delivered[path]
                else:
                    with contextlib.suppress(Exception):
                        self.bus.call(MM, self._modem(), MESSAGING, "Delete", path)
                        del self._delivered[path]
                    continue
            if pdu_type not in (1, 32) or state != 3:
                continue
            try:
                essentials.receive_message(number, text)
            except ValueError:
                continue
            delivered += 1
            try:
                self.bus.call(MM, self._modem(), MESSAGING, "Delete", path)
            except Exception:
                self._delivered[path] = identity
        return delivered


class FakeModem:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.inbox: list[tuple[str, str]] = []
        self.in_call = False
        self.fail_sms = False
        self.active_call: str | None = None

    def dial(self, number: str) -> None:
        if self.in_call:
            raise OSError("already in a call")
        self.calls.append(number)
        self.active_call = number
        self.in_call = True

    def hang_up(self) -> None:
        self.in_call = False
        self.active_call = None

    def send_sms(self, number: str, text: str) -> None:
        if self.fail_sms:
            raise OSError("no signal")
        self.sent.append((number, text))

    def deliver(self, number: str, text: str) -> None:
        self.inbox.append((number, text))

    def poll_incoming(self, essentials: Any) -> int:
        delivered = 0
        for number, text in list(self.inbox):
            try:
                essentials.receive_message(number, text)
            except ValueError:
                continue
            self.inbox.remove((number, text))
            delivered += 1
        return delivered


def default_modem(allow_fake: bool = False) -> MMModem | FakeModem:
    if shutil.which("gdbus") and os.path.exists("/run/dbus/system_bus_socket") and sys.platform.startswith("linux"):
        return MMModem(GdbusBus())
    if allow_fake:
        return FakeModem()
    raise OSError("no ModemManager available")
