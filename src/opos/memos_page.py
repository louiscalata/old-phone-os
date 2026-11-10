# SPDX-License-Identifier: GPL-3.0-or-later
"""Voice memos kept on this phone. Recording uses the page's microphone, not a new package.

The app is on only when OPOS_APPS names memos. Music does not turn it on.
The helper is not given a way to write a memo.
"""
import base64
import binascii
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from opos import appdoc, apps
from opos.store import default_root

_MAX = 50
_MAX_BYTES = 256 * 1024
_CHUNK = 1200
_MIMES = {"audio/webm": ".webm", "audio/wav": ".wav", "audio/ogg": ".ogg"}
_TYPES = {ext: mime for mime, ext in _MIMES.items()}
_OPEN = {}
_POWER = {"level": 100.0, "charging": False, "temp_c": 25.0}

_CSS = """
#rows button{text-align:left}
audio{display:none}
"""

_LIST = """
<h1>Memos</h1>
<p id="why" class="note" role="status" hidden></p>
<ul id="rows"></ul>
"""

_THUMB = """
<h2>Voice memos</h2>
<p>A recording stays on this phone.</p>
<button id="record" type="button">Record</button>
<button id="stop" type="button" hidden>Stop</button>
<audio id="player" preload="none"></audio>
<p id="line" role="status"></p>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
var rec=null, parts=[], stream=null, last=null;
function api(b){return fetch('/api/memos',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json().then(function(j){j._status=r.status;return j})})}
function b64(bytes){var s='';for(var i=0;i<bytes.length;i++)s+=String.fromCharCode(bytes[i]);return btoa(s)}
function say(text){var line=G('why');line.hidden=!text;line.textContent=text||''}
function paint(s){
  if(!s||s.error){if(s&&s.reason)say(s.reason);return}
  last=s;say(s.reason||'');
  G('record').disabled=!!s.paused;
  var rows=G('rows');rows.textContent='';
  if(!(s.memos||[]).length)rows.appendChild(el('li',null,'No memos.'));
  (s.memos||[]).forEach(function(memo){
    var li=document.createElement('li');
    var play=el('button',null,memo.title);play.type='button';
    play.onclick=function(){var audio=G('player');audio.src='/media/memos/'+memo.id;audio.play().catch(function(){})};
    var del=el('button',null,'Delete');del.type='button';
    del.onclick=function(){if(del.dataset.sure!=='1'){del.dataset.sure='1';del.textContent='Delete this memo?';return}api({action:'delete',id:memo.id}).then(paint)};
    li.appendChild(play);li.appendChild(del);rows.appendChild(li);
  });
}
function upload(blob){
  var reader=new FileReader();
  reader.onload=function(){
    var bytes=new Uint8Array(reader.result);
    api({action:'begin',mime:blob.type||'audio/webm'}).then(function(s){
      if(!s||s.error){say((s&&s.reason)||'Could not save.');return}
      var i=0,n=0;
      function next(){
        if(i>=bytes.length)return api({action:'finish',id:s.id,count:n}).then(paint);
        var slice=bytes.subarray(i,Math.min(bytes.length,i+1200));i+=slice.length;
        return api({action:'chunk',id:s.id,n:n,data:b64(slice)}).then(function(step){
          if(!step||step.error){say('Could not save.');return}
          n+=1;return next();
        });
      }
      return next();
    });
  };
  reader.readAsArrayBuffer(blob);
}
function stopTracks(){if(stream){stream.getTracks().forEach(function(t){t.stop()});stream=null}}
G('record').onclick=function(){
  if(last&&last.paused)return;
  if(!navigator.mediaDevices||!window.MediaRecorder){say('Microphone unavailable: this page cannot record.');G('record').disabled=true;return}
  navigator.mediaDevices.getUserMedia({audio:true}).then(function(live){
    stream=live;parts=[];
    var mime=MediaRecorder.isTypeSupported&&MediaRecorder.isTypeSupported('audio/webm')?'audio/webm':'';
    rec=mime?new MediaRecorder(live,{mimeType:mime}):new MediaRecorder(live);
    rec.ondataavailable=function(e){if(e.data&&e.data.size)parts.push(e.data)};
    rec.onstop=function(){var blob=new Blob(parts,{type:rec.mimeType||'audio/webm'});stopTracks();G('stop').hidden=true;G('record').hidden=false;upload(blob)};
    rec.start();G('record').hidden=true;G('stop').hidden=false;say('');
  }).catch(function(){say('Microphone unavailable: permission was not given.');G('record').disabled=true});
};
G('stop').onclick=function(){if(rec&&rec.state==='recording')rec.stop()};
api().then(paint);
window.oposMemos={refresh:function(){return api().then(paint)}};
"""


def reset():
    """Tests drop an unfinished recording and a pretended battery."""
    _OPEN.clear()
    _POWER.update(level=100.0, charging=False, temp_c=25.0)


def folder():
    raw = os.environ.get("OPOS_MEMOS_DIR")
    if raw:
        return Path(raw)
    return Path(os.environ.get("OPOS_STATE_DIR") or default_root()) / "memos"


def set_power(level, charging=False, temp_c=25.0):
    _POWER["level"] = float(level)
    _POWER["charging"] = bool(charging)
    _POWER["temp_c"] = float(temp_c)


def observe(handler):
    phone = getattr(handler, "phone", None)
    sim = getattr(phone, "sim", None)
    if sim is None:
        return
    set_power(getattr(sim, "level", 100.0), getattr(phone, "charging", False), getattr(sim, "temp_c", 25.0))


def _reason():
    return apps.pause_reason(
        "memos", _POWER["level"], charging=_POWER["charging"], temp_c=_POWER["temp_c"], in_use=True,
    )


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _rows():
    base = folder()
    if not base.is_dir():
        return []
    found = []
    for path in base.iterdir():
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in _TYPES:
            continue
        ident = path.stem[5:] if path.stem.startswith("memo-") else ""
        if len(ident) != 8 or any(c not in "0123456789abcdef" for c in ident):
            continue
        try:
            when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        found.append({"id": ident, "title": "Memo " + when.strftime("%H:%M"), "path": path, "when": when})
    found.sort(key=lambda row: row["when"], reverse=True)
    return found[:_MAX]


def snapshot():
    reason = _reason() or ""
    memos = [{"id": row["id"], "title": row["title"]} for row in _rows()]
    return {"ok": True, "paused": bool(reason), "reason": reason, "memos": memos}


def open_file(ident):
    if not isinstance(ident, str):
        return None
    for row in _rows():
        if row["id"] != ident:
            continue
        try:
            body = row["path"].read_bytes()
        except OSError:
            return None
        return body, _TYPES[row["path"].suffix.lower()]
    return None


def headers(received):
    policy = received.get("Content-Security-Policy", "")
    if "media-src" not in policy:
        received["Content-Security-Policy"] = policy + "; media-src 'self' blob: data:"
    received["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    return received


def _paused():
    reason = _reason()
    if not reason:
        return None
    return 403, {"error": "paused", "reason": reason}


def post(data):
    action = data.get("action") if isinstance(data, dict) else None
    refused = _paused()
    if refused is not None and action in {"begin", "chunk", "finish"}:
        return refused
    try:
        if action == "begin":
            return _begin(data.get("mime"))
        if action == "chunk":
            return _chunk(data.get("id"), data.get("n"), data.get("data"))
        if action == "finish":
            return _finish(data.get("id"), data.get("count"))
        if action == "delete":
            return _delete(data.get("id"))
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in {"mime", "id", "full", "data", "count"} else "bad request"}
    return 400, {"error": "bad request"}


def _begin(mime):
    if mime not in _MIMES:
        raise ValueError("mime")
    if len(_rows()) >= _MAX or len(_OPEN) >= 4:
        raise ValueError("full")
    ident = uuid.uuid4().hex[:8]
    _OPEN[ident] = {"mime": mime, "chunks": {}}
    return 200, dict(snapshot(), id=ident)


def _chunk(ident, number, data):
    slot = _OPEN.get(ident)
    if slot is None:
        raise ValueError("id")
    if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number > 400:
        raise ValueError("data")
    if not isinstance(data, str) or len(data) > 2000:
        raise ValueError("data")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("data") from None
    if not raw or len(raw) > _CHUNK:
        raise ValueError("data")
    held = sum(len(item) for item in slot["chunks"].values()) + len(raw)
    if held > _MAX_BYTES:
        raise ValueError("full")
    slot["chunks"][number] = raw
    return 200, {"ok": True}


def _finish(ident, count):
    slot = _OPEN.get(ident)
    if slot is None:
        raise ValueError("id")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count")
    if set(slot["chunks"]) != set(range(count)):
        raise ValueError("count")
    raw = b"".join(slot["chunks"][index] for index in range(count))
    if len(raw) > _MAX_BYTES:
        raise ValueError("full")
    path = folder() / ("memo-" + ident + _MIMES[slot["mime"]])
    _write(path, raw)
    _OPEN.pop(ident, None)
    return 200, snapshot()


def _delete(ident):
    if not isinstance(ident, str):
        raise ValueError("id")
    for row in _rows():
        if row["id"] == ident:
            try:
                row["path"].unlink()
            except OSError:
                raise ValueError("id") from None
            return 200, snapshot()
    raise ValueError("id")


def render(token, nonce):
    return appdoc.render("Memos", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


_APP = apps.register(apps.App(
    "memos", "Memos", "/memos",
    "Voice memos kept on this phone.",
    "yes", "no", "microphone, speaker", "real", "memos", "OPTIONAL", 1,
    render, snapshot, post,
))
_APP.open_file = open_file
_APP.observe = observe
_APP.headers = headers
