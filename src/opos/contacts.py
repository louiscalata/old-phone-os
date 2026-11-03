# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass
from opos.essentials import normalize_number

@dataclass(frozen=True)
class Contact:
    name: str
    number: str

class Book:
    def __init__(self, contacts=()):
        self._map = {}
        for c in contacts:
            self._map[c.number] = c

    def add(self, name, number):
        name = name.strip()
        if not name:
            raise ValueError
        norm = normalize_number(number)
        contact = Contact(name, norm)
        self._map[norm] = contact
        return contact

    def name_for(self, number):
        try:
            norm = normalize_number(number)
        except ValueError:
            return number
        c = self._map.get(norm)
        return c.name if c else norm

    def all(self):
        return list(self._map.values())

def threads(book, messages):
    data = {}
    for i, m in enumerate(messages):
        try:
            num = normalize_number(m.number)
        except ValueError:
            num = m.number
        e = data.get(num)
        if e is None:
            e = {'count': 0, 'unsent': 0, 'last': '', 'last_idx': -1}
            data[num] = e
        e['count'] += 1
        if m.outgoing and not m.sent:
            e['unsent'] += 1
        e['last'] = m.text
        e['last_idx'] = i
    out = []
    for num, e in data.items():
        out.append({
            'name': book.name_for(num),
            'number': num,
            'last': e['last'],
            'count': e['count'],
            'unsent': e['unsent'],
            '_idx': e['last_idx']
        })
    out.sort(key=lambda d: d['_idx'], reverse=True)
    for d in out:
        del d['_idx']
    return out

def thread(book, messages, number):
    try:
        target = normalize_number(number)
    except ValueError:
        target = number
    lines = []
    for m in messages:
        try:
            num = normalize_number(m.number)
        except ValueError:
            num = m.number
        if num != target:
            continue
        if m.outgoing:
            line = f"Me: {m.text}"
            if not m.sent:
                line += " (not sent)"
            lines.append(line)
        else:
            name = book.name_for(num)
            lines.append(f"{name}: {m.text}")
    return lines

DEMO_CONTACTS = (
    Contact("Mom", "+15550100"),
    Contact("Clinic", "+15550142"),
    Contact("Work", "+15550199"),
)
