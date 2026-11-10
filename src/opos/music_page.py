# SPDX-License-Identifier: GPL-3.0-or-later
"""Music kept in a folder on this phone. Playback is the page's audio element.

A hard battery floor pauses play and says why. The sleep timer is the Clock timer.
This module does not record and does not write calendar entries.
"""
import hashlib
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from opos import alarms, appdoc, apps
from opos.store import default_root

_AUDIO = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav"}
_MAX_BYTES = 32 * 1024 * 1024
_POWER = {"level": 100.0, "charging": False, "temp_c": 25.0}
_FAKE = None

_CSS = """
#rows button{text-align:left}
#pos{font:700 2.25rem/1.1 Georgia,'Times New Roman',serif;margin:8px 0}
audio{display:none}
"""

_LIST = """
<h1>Music</h1>
<p id="why" class="note" role="status" hidden></p>
<ul id="rows"></ul>
"""

_THUMB = """
<h2 id="track">Nothing playing</h2>
<p id="pos">0:00 / 0:00</p>
<audio id="player" preload="metadata"></audio>
<button id="play" type="button">Play</button>
<button id="prev" type="button">Previous</button>
<button id="next" type="button">Next</button>
<p>Sleep timer. This uses the Clock timer.</p>
<button id="s15" type="button">15 min</button>
<button id="s30" type="button">30 min</button>
<button id="soff" type="button">Off</button>
<p id="sleep" role="status"></p>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
var last=null, order=[], at=0, playing=false;
function api(b){return fetch('/api/music',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json()})}
function fmt(s){s=Math.max(0,Math.floor(+s||0));return Math.floor(s/60)+':'+(s%60<10?'0':'')+(s%60)}
function paint(s){
  if(!s||s.error)return;
  last=s;order=[];
  var rows=G('rows');rows.textContent='';
  var albums=s.albums||[];
  if(!albums.length)rows.appendChild(el('li',null,'No music files.'));
  albums.forEach(function(album){
    rows.appendChild(el('li',null,album.name));
    (album.tracks||[]).forEach(function(track){
      var i=order.length;order.push(track);
      var li=document.createElement('li'),b=el('button',null,track.title);b.type='button';
      b.onclick=function(){choose(i)};li.appendChild(b);rows.appendChild(li);
    });
  });
  var why=G('why');why.hidden=!s.reason;why.textContent=s.reason||'';
  G('play').disabled=!!s.paused;
  G('sleep').textContent=s.sleep||'';
  if(s.paused&&playing){playing=false;G('player').pause();G('play').textContent='Play'}
}
function choose(i){
  at=i;var track=order[i];if(!track)return;
  if(track.too_long){G('why').hidden=false;G('why').textContent='This file is too long to play here.';return}
  G('track').textContent=track.title;
  var audio=G('player');
  audio.src='/media/music/'+track.id+'?t='+encodeURIComponent(T);
  G('pos').textContent='0:00 / '+fmt(track.seconds);
  if(last&&last.paused){G('play').textContent='Play';return}
  playing=true;G('play').textContent='Pause';
  audio.play().catch(function(){playing=false;G('play').textContent='Play'});
}
function step(dir){if(!order.length)return;choose((at+dir+order.length)%order.length)}
G('play').onclick=function(){
  if(last&&last.paused)return;
  var audio=G('player');
  if(!audio.src){if(order.length)choose(0);return}
  playing=!playing;G('play').textContent=playing?'Pause':'Play';
  if(playing)audio.play().catch(function(){playing=false;G('play').textContent='Play'});else audio.pause();
};
G('prev').onclick=function(){step(-1)};
G('next').onclick=function(){step(1)};
G('player').ontimeupdate=function(){var a=G('player'),total=isFinite(a.duration)?a.duration:0;G('pos').textContent=fmt(a.currentTime)+' / '+fmt(total)};
G('s15').onclick=function(){api({action:'sleep',minutes:15}).then(paint)};
G('s30').onclick=function(){api({action:'sleep',minutes:30}).then(paint)};
G('soff').onclick=function(){api({action:'sleep',minutes:0}).then(paint)};
api().then(paint);
setInterval(function(){api().then(paint)},5000);
window.oposMusic={refresh:function(){return api().then(paint)}};
"""


def reset():
    """Tests drop a pretended clock and a pretended battery."""
    global _FAKE
    _FAKE = None
    _POWER.update(level=100.0, charging=False, temp_c=25.0)


def root():
    raw = os.environ.get("OPOS_MUSIC_DIR")
    if raw:
        return Path(raw)
    return Path.home() / "OPOS" / "Music"


def set_power(level, charging=False, temp_c=25.0):
    """A test, or observe(), says what the battery is doing."""
    _POWER["level"] = float(level)
    _POWER["charging"] = bool(charging)
    _POWER["temp_c"] = float(temp_c)


def observe(handler):
    phone = getattr(handler, "phone", None)
    sim = getattr(phone, "sim", None)
    if sim is None:
        return
    set_power(getattr(sim, "level", 100.0), getattr(phone, "charging", False), getattr(sim, "temp_c", 25.0))


def current_now():
    if os.environ.get("OPOS_MUSIC_TEST") == "1" and _FAKE is not None:
        return _FAKE
    return datetime.now(timezone.utc)


def _timer_book():
    """The Clock timer when that book is already this phone's file. Otherwise the same file."""
    state = Path(os.environ.get("OPOS_STATE_DIR") or default_root())
    loaded = sys.modules.get("opos.clock_page")
    existing = getattr(loaded, "_BOOK", None) if loaded is not None else None
    if existing is not None and Path(existing.store.root) == state:
        return existing
    return alarms.Book(state, ZoneInfo("UTC"))


def _seconds(path):
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate() or 1
            return round(handle.getnframes() / float(rate), 3)
    except (wave.Error, OSError, EOFError):
        return None


def _scan():
    folder = root()
    if not folder.is_dir():
        return []
    base = folder.resolve()
    found = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name.startswith("."):
            continue
        kind = path.suffix.lower()
        if kind not in _AUDIO:
            continue
        try:
            rel = path.resolve().relative_to(base)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        album = rel.parts[0] if len(rel.parts) > 1 else "Music"
        title = path.stem[:80] or "Track"
        ident = hashlib.sha256(rel.as_posix().encode("utf-8")).hexdigest()[:12]
        found.append({
            "id": ident, "title": title, "album": album[:80], "seconds": _seconds(path),
            "too_long": size > _MAX_BYTES, "path": path, "kind": kind,
        })
    return found


def _public():
    albums = []
    index = {}
    for track in _scan():
        index.setdefault(track["album"], []).append({
            "id": track["id"], "title": track["title"], "seconds": track["seconds"], "too_long": track["too_long"],
        })
    for name in sorted(index):
        albums.append({"name": name, "tracks": index[name]})
    return albums


def _reason():
    return apps.pause_reason(
        "music", _POWER["level"], charging=_POWER["charging"], temp_c=_POWER["temp_c"], in_use=True,
    )


def _sleep():
    timer = _timer_book().timer
    if not isinstance(timer, dict) or timer.get("label") != "Sleep":
        return "", False
    try:
        fires = datetime.fromisoformat(timer["fires_at"])
    except (TypeError, ValueError):
        return "", False
    if fires.tzinfo is None:
        fires = fires.replace(tzinfo=timezone.utc)
    if current_now() >= fires:
        return "Sleep timer: paused", True
    return "Sleep: On", False


def snapshot():
    battery = _reason()
    sleep, due = _sleep()
    if battery:
        paused, reason = True, battery
    elif due:
        paused, reason = True, sleep
    else:
        paused, reason = False, ""
    return {"ok": True, "paused": paused, "reason": reason, "sleep": sleep, "albums": _public()}


def open_file(ident):
    if not isinstance(ident, str):
        return None
    for track in _scan():
        if track["id"] != ident or track["too_long"]:
            continue
        try:
            body = track["path"].read_bytes()
        except OSError:
            return None
        return body, _AUDIO[track["kind"]]
    return None


def headers(received):
    policy = received.get("Content-Security-Policy", "")
    if "media-src" not in policy:
        received["Content-Security-Policy"] = policy + "; media-src 'self'"
    return received


def post(data):
    global _FAKE
    action = data.get("action") if isinstance(data, dict) else None
    if action != "sleep":
        return 400, {"error": "bad request"}
    minutes = data.get("minutes")
    try:
        saved = _timer_book()
        if minutes == 0:
            if isinstance(saved.timer, dict) and saved.timer.get("label") == "Sleep":
                saved.cancel_timer()
        elif minutes in (15, 30, 60):
            saved.start_timer(int(minutes) * 60, current_now(), "Sleep")
        else:
            raise ValueError("minutes")
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in {"minutes", "seconds", "now"} else "bad request"}
    return 200, snapshot()


def render(token, nonce):
    return appdoc.render("Music", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


_APP = apps.register(apps.App(
    "music", "Music", "/music",
    "Music files kept on this phone.",
    "yes", "no", "speaker", "real", "music", "OPTIONAL", 1,
    render, snapshot, post,
))
_APP.open_file = open_file
_APP.observe = observe
_APP.headers = headers
