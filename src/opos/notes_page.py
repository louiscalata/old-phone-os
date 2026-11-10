# SPDX-License-Identifier: GPL-3.0-or-later
"""Notes. One text file per note, kept on the phone. Plain text only."""
import os
import uuid
from datetime import datetime, timezone

from opos import appdoc, apps
from opos.store import Store, default_root

_STORE = None
_CAP = 64 * 1024
_MAX = 200

_CSS = """
#body{width:100%;min-height:12rem;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
"""
_LIST = """<h1>Notes</h1><ul id="rows"></ul>"""
_THUMB = """
<p id="saved" role="status"></p>
<textarea id="body" maxlength="65536" aria-label="Note"></textarea>
<button id="fresh" type="button">New note</button>
"""
_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content, notes=[], cur=null, timer=null;
function api(b){return fetch('/api/notes',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json()})}
function paint(s){if(!s||s.error){G('saved').textContent=s&&s.error?s.error:'';return}notes=s.notes||[];var rows=G('rows');rows.textContent='';notes.forEach(function(n){var li=document.createElement('li'),b=el('button',null,n.title||'Empty note');b.type='button';b.onclick=function(){open(n.id)};li.appendChild(b);rows.appendChild(li)});if(s.saved)G('saved').textContent=s.saved;if(s.text!=null&&cur)G('body').value=s.text}
function open(id){cur=id;api({action:'open',id:id}).then(paint)}
G('fresh').onclick=function(){api({action:'add'}).then(function(s){cur=s.id;G('body').value='';paint(s)})};
G('body').oninput=function(){if(!cur)return;clearTimeout(timer);timer=setTimeout(function(){api({action:'save',id:cur,text:G('body').value}).then(paint)},400)};
api().then(function(s){paint(s);if(notes[0])open(notes[0].id)});
"""


def reset():
    global _STORE
    _STORE = None


def _store():
    global _STORE
    if _STORE is None:
        _STORE = Store(os.environ.get("OPOS_STATE_DIR") or default_root())
    return _STORE


def _index():
    data, note = _store().read_json("notes.json", {"notes": []})
    rows = data.get("notes") if isinstance(data.get("notes"), list) else []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)], note


def _write_index(rows):
    _store().write_json("notes.json", {"notes": rows})


def _title(text):
    line = (text.splitlines() or [""])[0].strip()
    return (line or "Empty note")[:80]


def _clock():
    return datetime.now(timezone.utc).strftime("%H:%M")


def snapshot(extra=None):
    rows, note = _index()
    out = {"ok": True, "notes": [{"id": row["id"], "title": row.get("title") or "Empty note"} for row in rows], "recovered": note}
    if extra:
        out.update(extra)
    return out


def post(data):
    action = data.get("action") if isinstance(data, dict) else None
    rows, _note = _index()
    try:
        if action == "add":
            if len(rows) >= _MAX:
                raise ValueError("full")
            ident = uuid.uuid4().hex[:8]
            rows.insert(0, {"id": ident, "title": "Empty note"})
            _write_index(rows)
            _store().write_bytes("note-%s.txt" % ident, b"", _CAP)
            return 200, dict(snapshot(), id=ident, text="", saved="")
        if action == "open":
            ident = data.get("id")
            if ident not in {row["id"] for row in rows}:
                raise ValueError("id")
            raw = (_store().root / ("note-%s.txt" % ident)).read_text(encoding="utf-8")
            return 200, dict(snapshot(), id=ident, text=raw)
        if action == "save":
            ident = data.get("id")
            text = data.get("text")
            if not isinstance(text, str):
                raise ValueError("text")
            raw = text.encode("utf-8")
            if len(raw) > _CAP:
                raise ValueError("size")
            found = next((row for row in rows if row["id"] == ident), None)
            if found is None:
                raise ValueError("id")
            _store().write_bytes("note-%s.txt" % ident, raw, _CAP)
            found["title"] = _title(text)
            _write_index(rows)
            return 200, dict(snapshot(), id=ident, saved="Saved " + _clock())
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in {"id", "text", "full", "size"} else "bad request"}
    return 400, {"error": "bad request"}


def render(token, nonce):
    return appdoc.render("Notes", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


apps.register(apps.App(
    "notes", "Notes", "/notes",
    "Plain text notes kept on this phone.",
    "yes", "no", "none", "real", "notes", "USEFUL", 0,
    render, snapshot, post,
))
