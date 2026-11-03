# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
import typing
from opos.secure import clean_text


class Modem(typing.Protocol):
    def dial(self, number: str) -> None: ...
    def hang_up(self) -> None: ...
    def send_sms(self, number: str, text: str) -> None: ...


def normalize_number(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError
    if len(raw) > 64:
        raise ValueError
    allowed = set("0123456789+ -().")
    if any(ch not in allowed for ch in raw):
        raise ValueError
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if cleaned.count("+") > 1 or ("+" in cleaned and not cleaned.startswith("+")):
        raise ValueError
    digits = [c for c in cleaned if c.isdigit()]
    if len(digits) < 3:
        raise ValueError
    return cleaned


def normalize_sender(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError
    try:
        return normalize_number(raw)
    except ValueError:
        pass
    if not raw.isascii() or not 1 <= len(raw) <= 64:
        raise ValueError
    if not any(ch.isalpha() for ch in raw):
        raise ValueError
    if raw.isalnum():
        return raw
    if len(raw) > 11:
        raise ValueError
    if any(not (ch.isalnum() or ch in " -_.") for ch in raw):
        raise ValueError
    return raw


MAX_LOG = 500
MAX_TEXT = 1600


@dataclass
class Message:
    number: str
    text: str
    outgoing: bool
    sent: bool = False


class Essentials:
    def __init__(self, modem):
        self.modem = modem
        self.in_call = False
        self.log = []
        self.outbox = []

    def call(self, number):
        if self.in_call:
            query = getattr(self.modem, "is_call_active", None)
            if not callable(query):
                raise RuntimeError
            try:
                active = query()
            except Exception:
                raise RuntimeError from None
            if active is not False:
                raise RuntimeError
            self.in_call = False
        norm = normalize_number(number)
        self.modem.dial(norm)
        self.in_call = True
        return norm

    def end_call(self):
        if self.in_call:
            self.modem.hang_up()
            self.in_call = False

    def send_message(self, number, text):
        if not isinstance(text, str):
            raise ValueError
        if not text or len(text) > 160:
            raise ValueError
        text = clean_text(text, 160)
        if not text:
            raise ValueError
        norm = normalize_number(number)
        if len(self.log) >= MAX_LOG:
            for i, m in enumerate(self.log):
                if not (m.outgoing and not m.sent):
                    del self.log[i]
                    break
            else:
                del self.log[0]
        msg = Message(number=norm, text=text, outgoing=True)
        try:
            self.modem.send_sms(norm, text)
            msg.sent = True
        except OSError:
            self.outbox.append(msg)
        self.log.append(msg)
        return msg

    def retry_outbox(self):
        sent_count = 0
        remaining = []
        for msg in self.outbox:
            try:
                self.modem.send_sms(msg.number, msg.text)
                msg.sent = True
                sent_count += 1
            except OSError:
                remaining.append(msg)
        self.outbox = remaining
        return sent_count

    def receive_message(self, number, text):
        norm = normalize_sender(number)
        if not isinstance(text, str):
            raise ValueError
        if not 1 <= len(text) <= MAX_TEXT:
            raise ValueError
        text = clean_text(text, MAX_TEXT)
        if not text:
            raise ValueError
        if len(self.log) >= MAX_LOG:
            for i, m in enumerate(self.log):
                if not (m.outgoing and not m.sent):
                    del self.log[i]
                    break
            else:
                del self.log[0]
        msg = Message(number=norm, text=text, outgoing=False)
        self.log.append(msg)
        return msg
