# SPDX-License-Identifier: GPL-3.0-or-later
"""Notes. One text file per note, kept on the phone. Plain text only."""
import os
import re
import uuid
from datetime import datetime, timezone

from opos import appdoc, apps
from opos.store import Store, default_root

_STORE = None
_CAP = 64 * 1024
_MAX = 200
_ID = re.compile(r"[0-9a-f]{8}")

_CSS = """
#body{width:100%;min-height:12rem;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
"""
_LIST = """<h1>Notes</h1><ul id="rows"></ul>"""
_THUMB = """
<p id="saved" role="status"></p>
<textarea id="body" maxlength="65536" aria-label="Note"></textarea>
<button id="fresh" type="button">New note</button>
<button id="del" type="button" hidden>Delete</button>
"""
_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content, notes=[], cur=null;
// Each draft and write belongs to its note, even after the selection changes.
var pending={}, writing={}, deleting={};
function api(b){return fetch('/api/notes',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json().then(function(s){if(!r.ok||!s||s.error)throw new Error(s&&s.error?s.error:'The note could not be updated.');return s})})}
function failed(e){G('saved').textContent=e&&e.message?e.message:'The note could not be updated.'}
function armDelete(){var d=G('del');d.hidden=!cur;d.disabled=!!deleting[cur];G('body').disabled=!!deleting[cur];if(!cur)return;d.dataset.sure='';d.textContent='Delete'}
function paint(s){if(!s||s.error){G('saved').textContent=s&&s.error?s.error:'';return}notes=s.notes||[];var rows=G('rows');rows.textContent='';notes.forEach(function(n){var li=document.createElement('li'),b=el('button',null,n.title||'Empty note');b.type='button';b.onclick=function(){open(n.id)};li.appendChild(b);rows.appendChild(li)});G('del').hidden=!cur;if(s.saved)G('saved').textContent=s.saved;if(s.text!=null&&cur)G('body').value=s.text}
function open(id){cur=id;armDelete();api({action:'open',id:id}).then(function(s){if(cur===id)paint(s)}).catch(function(e){if(cur===id)failed(e)})}
function save(id,text){
  var before=writing[id]||Promise.resolve();
  var job=before.catch(function(){}).then(function(){return api({action:'save',id:id,text:text})});
  writing[id]=job;
  job.then(function(s){if(writing[id]===job)delete writing[id];if(cur===id)paint(s)},function(e){if(writing[id]===job)delete writing[id];if(cur===id)failed(e)});
  return job;
}
function flush(id){
  var p=pending[id];
  if(p){clearTimeout(p.timer);delete pending[id];return save(id,p.text)}
  return writing[id]||Promise.resolve();
}
G('fresh').onclick=function(){api({action:'add'}).then(function(s){cur=s.id;G('body').value='';armDelete();paint(s)}).catch(failed)};
G('del').onclick=function(){
  if(!cur||deleting[cur])return;
  var d=G('del');
  if(d.dataset.sure!=='1'){d.dataset.sure='1';d.textContent='Delete this note?';return}
  var id=cur;deleting[id]=true;armDelete();
  // A save already queued for this note must finish before its deletion.
  flush(id).then(function(){return api({action:'delete',id:id})}).then(function(s){
    if(cur===id){cur=null;G('body').value='';armDelete()}
    paint(s);
  }).catch(failed).then(function(){
    delete deleting[id];if(cur===id||!cur)armDelete();
  });
};
G('body').oninput=function(){
  if(!cur||deleting[cur])return;
  var id=cur,text=G('body').value;
  if(pending[id])clearTimeout(pending[id].timer);
  pending[id]={text:text,timer:setTimeout(function(){delete pending[id];save(id,text)},400)};
};
api().then(function(s){paint(s);if(notes[0])open(notes[0].id)}).catch(failed);
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


def _remove_file(ident):
    """Drop the note file when the id is one this page created. A missing file is already gone."""
    if _ID.fullmatch(ident or "") is None:
        return
    path = _store().root / ("note-%s.txt" % ident)
    try:
        path.unlink()
    except FileNotFoundError:
        return


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
        if action == "delete":
            ident = data.get("id")
            if ident not in {row["id"] for row in rows}:
                raise ValueError("id")
            # Keep the row retryable if unlink fails. A missing file is already
            # deleted, so a later retry can also finish a failed index write.
            _remove_file(ident)
            _write_index([row for row in rows if row["id"] != ident])
            return 200, snapshot()
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
