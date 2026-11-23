# SPDX-License-Identifier: GPL-3.0-or-later
"""AI mode: two plain-text panes, durable answers and a local stream worker."""

from __future__ import annotations

import secrets
import threading
import time
from copy import deepcopy
from typing import Any

from opos import ai_answers, appdoc, apps, assist, muse

try:
    from opos import gpu_link_server
except ImportError:
    gpu_link_server = None

QUESTIONS = (
    "Explain why the sky looks blue in words a ten-year-old would understand.",
    "Suggest five things neighbours can do together to make a small public garden welcoming.",
    "Write a detailed guide to starting a public library of useful things, such as tools, games and kitchen equipment. "
    "Use twenty numbered sections, with about eighty words in each section. Cover choosing a space, "
    "asking neighbours what they need, collecting donations, checking safety, keeping a catalogue, borrowing rules, "
    "repairs, volunteers, accessibility, welcoming newcomers, costs, opening hours, teaching skills, sharing fairly, "
    "handling lost items, measuring usefulness and planning the next year. Give practical examples throughout, "
    "and finish with a sample plan for the first month.",
)
PRESETS = tuple(
    {"label": label, "question": question}
    for label, question in zip(
        ("Why the sky is blue", "A welcoming garden", "A long guide (20 sections)"), QUESTIONS, strict=True
    )
)
DISCLOSURE = "Compare answers here and on an enrolled PC. This PC can see what you ask here."
APPEND_INTERVAL = 0.25


def _route() -> muse.Route | None:
    return assist.local_route(free=assist.free_mb())


def _service() -> Any:
    return gpu_link_server.service() if gpu_link_server is not None else None


class PhoneWorker:
    """Own one answer's thread and close its stream in the reading thread."""

    def __init__(self, answers: ai_answers.AnswerStore, ident: str) -> None:
        self.answers = answers
        self.ident = ident
        self.cancelled = threading.Event()
        self.problem: str | None = None
        self.live_speed: int | None = None
        self.thread = threading.Thread(target=self._run, name="ai-phone-" + ident, daemon=True)

    def start(self) -> None:
        """Start this answer exactly once."""
        self.thread.start()

    def _wait_turn(self) -> bool:
        while not self.cancelled.is_set():
            answer = self.answers.get(self.ident)
            if answer is None or answer["state"] in ai_answers.FINAL:
                return False
            if answer["state"] == "waiting":
                return True
            self.cancelled.wait(0.05)
        return False

    def _end(self, code: str) -> None:
        answer = self.answers.get(self.ident)
        if answer is not None and answer["state"] not in ai_answers.FINAL:
            if self.cancelled.is_set():
                self.answers.cancel(self.ident)
            else:
                self.answers.fail(self.ident, code)

    def _read(self, route: muse.Route, question: str) -> None:
        offset = 0
        pending = b""
        last = float("-inf")
        first_piece = None
        last_speed = 0.0
        pieces = 0
        try:
            stream = muse.stream(route, question, cancel=self.cancelled, max_tokens=4096)
        except muse.MuseError:
            self._end("no_model")
            return
        with stream:
            self.answers.note(self.ident, model=stream.route.name[:64], where=stream.route.where[:64])
            for piece in stream:
                if self.cancelled.is_set():
                    return
                now = time.monotonic()
                pieces += 1
                if first_piece is None:
                    first_piece = last_speed = now
                if now - last_speed >= 1:
                    self.live_speed = round(pieces / (now - first_piece))
                    last_speed = now
                pending += ai_answers.clean(piece).encode("utf-8")
                if offset + len(pending) > ai_answers.MAX_ANSWER_BYTES:
                    raise ai_answers.TooLongError(offset)
                if pending and time.monotonic() - last >= APPEND_INTERVAL:
                    self.answers.append(self.ident, offset, pending)
                    offset += len(pending)
                    pending = b""
                    last = time.monotonic()
            if self.cancelled.wait(max(0, APPEND_INTERVAL - (time.monotonic() - last))):
                return
            self.answers.note(self.ident, tokens=stream.tokens, tps=stream.tps)
            self.answers.append(self.ident, offset, pending, done=True, finish=stream.finish or "stop")

    def _run(self) -> None:
        code = None
        try:
            if not self._wait_turn():
                return
            route = _route()
            if route is None:
                code = "no_model"
                return
            answer = self.answers.begin(self.ident)
            self._read(route, answer["question"])
        except ai_answers.TooLongError:
            code = "too_long"
        except Exception:  # Thread boundary: persist a safe code, never prompt or error text.
            code = "stream_error"
        finally:
            self.live_speed = None
            if code is not None or self.cancelled.is_set():
                try:
                    self._end(code or "cancelled")
                except Exception:  # Thread boundary: retain a safe failure for the next snapshot to retry.
                    self.problem = "storage_error"


class PageState:
    """Serialize page actions and number complete display snapshots."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.boot = secrets.token_hex(4)
        self.rev = 0
        self.previous: dict | None = None
        self.workers: dict[str, PhoneWorker] = {}

    def _views(self, answers: ai_answers.AnswerStore, link: Any) -> tuple[dict | None, dict | None, dict | None, int]:
        # The public APIs offer no shared lock. Retry overlapping writes rather
        # than publishing a phone answer and a GPU answer from different revisions.
        for _ in range(20):
            before = link.snapshot() if link is not None else None
            revision = answers.rev
            phone = answers.current("phone") or answers.latest("phone")
            gpu = answers.current("gpu") or answers.latest("gpu")
            after = link.snapshot() if link is not None else None
            if revision == answers.rev and before == after:
                return phone, gpu, after, revision
        raise OSError("AI mode is busy; try again.")

    def snapshot(self) -> dict:
        """Return both panes together, with a revision for store and link changes."""
        with self.lock:
            for job in self.workers.values():
                if not job.thread.is_alive() and job.problem is not None:
                    # Never invent an answer outside the store. If storage is
                    # still unavailable, refuse the snapshot instead of hiding it.
                    job._end(job.problem)
                    job.problem = None
            self.workers = {key: job for key, job in self.workers.items() if job.thread.is_alive()}
            phone, gpu, link, revision = self._views(ai_answers.shared(), _service())
            route = _route()
            model = link["model"] if link is not None else None
            pc = link["pc"] if link is not None else None
            body = {
                "phone": {
                    "model": (phone["model"] if phone else "") or (route.name if route else ""),
                    "where": (phone["where"] if phone else "") or (route.where if route else "on this phone"),
                    "ready": route is not None and not (phone and phone["error"] == "no_model"),
                    "answer": phone,
                    "live_speed": (
                        self.workers[phone["id"]].live_speed
                        if phone and phone["state"] == "answering" and phone["id"] in self.workers
                        else None
                    ),
                },
                "gpu": {
                    "model": (gpu["model"] if gpu else "") or (model["name"] if model else ""),
                    "where": (gpu["where"] if gpu else "") or "on the PC's GPU",
                    "ready": bool(
                        model
                        and model["state"] == "ready"
                        and pc
                        and pc["live"]
                        and pc["state"] == "ready"
                        and link.get("storage") != "error"
                    ),
                    "answer": gpu,
                    "link": link,
                },
                "presets": deepcopy(list(PRESETS)),
            }
            # Include store rev even if a change affects an older queued answer.
            comparison = {"store_rev": revision, **body}
            if comparison != self.previous:
                self.rev += 1
                self.previous = deepcopy(comparison)
            return {"boot": self.boot, "rev": self.rev, **body}

    def ask(self, data: dict) -> None:
        """Validate once, store each question and start each phone answer's worker."""
        question = data.get("question")
        panes = data.get("panes")
        if not isinstance(question, str):
            raise ValueError("Enter a question.")
        question = ai_answers.clean(question)
        if not question.strip():
            raise ValueError("Enter a question.")
        if len(question.encode("utf-8")) > ai_answers.MAX_QUESTION_BYTES:
            raise ValueError("Keep the question to 4,096 bytes or fewer.")
        if not isinstance(panes, list) or not panes or any(pane not in ai_answers.PANES for pane in panes):
            raise ValueError("Choose the phone, the GPU, or both.")
        if len(set(panes)) != len(panes):
            raise ValueError("Choose each pane once.")
        answers = ai_answers.shared()
        panes = [pane for pane in panes if pane != "gpu" or _service() is not None]
        if not panes:
            raise ValueError("GPU Link is off.")
        if "gpu" in panes and answers.startup_notice:
            raise ValueError(answers.startup_notice)
        if any(answers.current(pane) is not None for pane in panes):
            raise ValueError("Wait for this answer, or stop it.")
        held = answers.snapshot()["answers"]
        unfinished = sum(row["state"] not in ai_answers.FINAL for row in held)
        if unfinished + len(panes) > ai_answers.MAX_ANSWERS:
            raise ai_answers.FullError("full")
        for pane in panes:
            answer = answers.ask(pane, question)
            if pane == "phone":
                job = PhoneWorker(answers, answer["id"])
                self.workers[answer["id"]] = job
                try:
                    job.start()
                except RuntimeError:
                    try:
                        answers.fail(answer["id"], "worker_start")
                    except OSError:
                        job.problem = "worker_start"
                        raise
                    del self.workers[answer["id"]]

    def cancel(self, ident: str) -> None:
        """Stop local work or ask the link to drop a GPU lease before cancelling."""
        answers = ai_answers.shared()
        answer = answers.get(ident) if isinstance(ident, str) else None
        if answer is None:
            raise ValueError("That answer was not found.")
        job = self.workers.get(ident)
        if job is not None:
            job.cancelled.set()
        link = _service()
        if answer["pane"] == "gpu" and link is not None:
            link.cancel(ident)
        else:
            answers.cancel(ident)

    def close(self) -> None:
        """Cancel and join owned workers; tests call this before removing storage."""
        with self.lock:
            jobs = list(self.workers.values())
            for job in jobs:
                job.cancelled.set()
        for job in jobs:
            if job.thread.ident is not None:
                job.thread.join(muse.STREAM_IDLE_S + 5)
            if job.thread.is_alive():
                raise RuntimeError("AI mode worker did not stop.")
        with self.lock:
            self.workers.clear()


_STATE = PageState()


def snapshot() -> dict:
    """Return the app's complete token-protected data."""
    return _STATE.snapshot()


def post(data: Any) -> tuple[int, dict]:
    """Apply one action and return a fresh snapshot, or a short refusal sentence."""
    if not isinstance(data, dict):
        return 400, {"error": "Choose an AI mode action."}
    with _STATE.lock:
        try:
            action = data.get("action")
            if action == "ask":
                _STATE.ask(data)
            elif action == "cancel":
                _STATE.cancel(data.get("id"))
            elif action in ("allow", "deny", "reopen", "forget"):
                link = _service()
                if link is None:
                    raise ValueError("GPU Link is off.")
                if action in ("allow", "deny"):
                    if getattr(link, action)(data.get("code")) is False:
                        raise ValueError("That request is no longer waiting.")
                elif getattr(link, action)() is False:
                    raise ValueError("There is no PC request to change.")
            else:
                raise ValueError("Choose an AI mode action.")
            return 200, snapshot()
        except ai_answers.FullError:
            return 400, {"error": "Stop an unfinished answer before asking again."}
        except ai_answers.StateError:
            return 400, {"error": "That answer has already ended."}
        except OSError:
            return 500, {"error": "The phone could not save the answer."}
        except ValueError as err:
            return 400, {"error": str(err)}


# Inline assets follow the existing app-page pattern: this lane owns no shared
# static route, and appdoc.render accepts its script and CSS as strings.
_CSS = """
html,body,button,input{font-family:"Inter Tight",sans-serif;font-size:max(18px,1rem)}
h1,h2,.speed{font-family:Newsreader,serif}
#list,#thumb{min-width:0}
.pane{min-width:0;margin-top:16px}
.pane p{margin:0 0 8px}
.speed{font-size:32px;line-height:1.15}
.answer{white-space:pre-wrap;overflow-wrap:anywhere;height:30vh;max-height:30vh;overflow-y:auto;box-sizing:border-box;border:1px solid var(--fg);padding:12px}
#err:not(:empty){border:3px double var(--fg);padding:8px}
#question{width:100%;min-height:48px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);font:inherit}
#controls{margin-bottom:16px}
#presets{display:flex;flex-wrap:wrap;gap:8px}
#presets button{font:inherit;width:auto;margin:0;padding:8px}
button:disabled{opacity:.6;cursor:wait}
button:disabled::after{content:"\u00a0unavailable"}
#gpu-cable{font-size:24px;font-weight:600;border:1px solid var(--fg);padding:6px 10px}
#gpu-cable[data-cable=wait]{border:3px solid var(--fg)}
#gpu-cable[data-cable=out]{border:3px double var(--fg)}
[hidden]{display:none!important}
html[data-pane] main{display:block}
html[data-pane] #gap{display:none}
html[data-pane=phone] #thumb,html[data-pane=gpu] #list{display:none}
html[data-pane] #list,html[data-pane] #thumb{padding:0}
@media(min-width:1000px){
html:not([data-pane]) #list,html:not([data-pane]) #thumb{display:contents}
html:not([data-pane]) #ai-title{grid-column:1;grid-row:1}
html:not([data-pane]) #controls{grid-column:1;grid-row:2}
html:not([data-pane]) #gap{grid-column:2;grid-row:1 / span 12}
html:not([data-pane]) .pane{display:grid;grid-template-rows:subgrid;grid-row:3 / span 9;margin-top:0}
html:not([data-pane]) #phone-pane{grid-column:1}
html:not([data-pane]) #gpu-pane{grid-column:3}
html:not([data-pane]) .pane h2{grid-row:1}
html:not([data-pane]) .model{grid-row:2}
html:not([data-pane]) .speed{grid-row:3}
html:not([data-pane]) .details{grid-row:4}
html:not([data-pane]) #gpu-cable{grid-row:5}
html:not([data-pane]) #gpu-disclosure{grid-row:6}
html:not([data-pane]) .answer{grid-row:7;height:55vh;max-height:55vh}
html:not([data-pane]) .stop{grid-row:8;align-self:start}
html:not([data-pane]) #link-controls{grid-row:9}
html:not([data-pane]) .nav{grid-column:3;grid-row:12}
}
@media(prefers-reduced-motion:reduce){main{transition:none;animation:none}}
"""

_LIST = """
<h1 id="ai-title">AI mode</h1>
<div id="controls">
<label for="question">Question</label><input id="question" type="text" autocomplete="off">
<button id="ask" type="button" class="main">Ask both models</button>
<div id="presets" aria-label="Preset questions"></div>
<p id="err" role="status"></p>
</div>
<section id="phone-pane" class="pane" aria-labelledby="phone-title">
<h2 id="phone-title">Phone</h2>
<p id="phone-model" class="model" hidden></p>
<p id="phone-speed" class="speed" role="status">Waiting for a snapshot</p>
<p id="phone-state" class="details" hidden></p>
<div id="phone-answer" class="answer" tabindex="0" role="region" aria-label="Phone answer"></div>
<button id="phone-stop" class="stop" type="button" hidden>Stop phone answer</button>
</section>
"""
_THUMB = """
<section id="gpu-pane" class="pane" aria-labelledby="gpu-title">
<h2 id="gpu-title">PC GPU</h2>
<p id="gpu-model" class="model" hidden></p>
<p id="gpu-speed" class="speed" role="status">Waiting for a snapshot</p>
<p id="gpu-state" class="details" hidden></p>
<p id="gpu-cable" role="status" hidden></p>
<p id="gpu-disclosure">This PC can see what you ask here.</p>
<div id="gpu-answer" class="answer" tabindex="0" role="region" aria-label="PC GPU answer"></div>
<button id="gpu-stop" class="stop" type="button" hidden>Stop GPU answer</button>
<div id="link-controls">
<div id="enrol" hidden><p id="enrol-text"></p>
<button id="deny" type="button" class="main">Not now</button>
<button id="allow" type="button">Allow</button></div>
<button id="reopen" type="button" hidden>Allow PC requests again</button>
<button id="forget" type="button" hidden>Forget this PC</button>
</div>
</section>
"""
_SCRIPT = r"""
function G(id){return document.getElementById(id)}
function text(id,value){var e=G(id);if(e.textContent!==value)e.textContent=value}
var pane=new URLSearchParams(location.search).get('pane');
if(pane!=='phone'&&pane!=='gpu')pane=null;
if(pane){document.documentElement.setAttribute('data-pane',pane);G(pane==='phone'?'thumb':'list').hidden=true}
if(pane==='gpu'){
 G('thumb').insertBefore(G('controls'),G('gpu-pane'));
 G('thumb').insertBefore(G('ai-title'),G('controls'));
}
if(pane==='phone')G('list').appendChild(G('home').parentNode);
var token=document.querySelector('meta[name=opos-token]').content;
var stale=false;
var boot=null,rev=-1,current=null,timer=null,busy=false,queue=[],last=-Infinity,open=false,pendingCode=null,presetKey=null;
var OPEN=['queued','waiting','loading','answering','paused'];
var ERRORS={no_model:'No model is available on this phone.',stream_error:'The phone model could not finish the answer.',
 too_long:'The answer reached the storage limit.',restart:'The phone restarted before the answer finished.',
 worker_start:'The phone could not start the answer.',storage_error:'The phone could not save the answer.'};
var PHONE_ONLY=['no_model','stream_error','restart','worker_start','storage_error'];
function errorWords(name,code){
 if(name!=='phone'&&PHONE_ONLY.indexOf(code)>=0)return '';
 return Object.prototype.hasOwnProperty.call(ERRORS,code)?ERRORS[code]:'';
}
function linkWords(link){
 if(!link)return 'GPU Link is off.';
 if(!link.pc)return link.enrol==='closed'?'PC requests are closed.':'Waiting for the PC';
 if(!link.pc.live)return 'Waiting for the PC';
 if(link.pc.state==='error')return 'The PC could not run the model.';
 if(link.pc.state==='copying')return 'Copying the model';
 if(link.pc.state==='loading')return 'Loading the model';
 if(link.pc.state==='stopping')return 'Stopping';
 if(link.pc.state==='idle')return 'The PC is not ready yet.';
 if(link.pc.state==='busy')return 'The PC is busy.';
 if(link.model.state==='checking')return 'Checking the model file';
 if(link.model.state==='error')return 'The model file could not be read.';
 if(link.model.state==='none')return 'No GPU model file is available.';
 return 'Ready';
}
function storageWords(link){return link.storage_notice||ERRORS.storage_error}
function words(name,p){
 if(name==='gpu'&&p.link&&p.link.storage==='error')return storageWords(p.link);
 var a=p.answer;
 if(a){
  if(a.state==='failed')return errorWords(name,a.error)||(name==='gpu'?'The PC could not finish the answer.':'The phone could not finish the answer.');
  if(a.state==='done')return a.finish==='length'?'Done: the model reached its answer limit.':'Done';
  if(a.state==='cancelled')return 'Stopped';
  if(a.state==='paused')return name==='gpu'?'Paused. It continues when the PC is ready.':'Paused';
  if(a.state==='queued')return 'Queued behind an earlier answer';
  if(a.state==='loading')return 'Loading the model';
  if(a.state==='answering')return 'Answering';
  if(name==='phone')return 'Waiting for the phone model';
  return linkWords(p.link)==='Ready'?'Waiting for the PC':linkWords(p.link);
 }
 return name==='gpu'?linkWords(p.link):(p.ready?'Ready':'No model is available on this phone.');
}
function cableState(p){
 var pc=p.link&&p.link.pc;
 if(!pc)return '';
 if(!pc.live)return 'out';
 return p.link.storage==='error'||pc.state==='copying'?'wait':'ok';
}
function cableWords(p){
 var state=cableState(p),a=p.answer,pc=p.link&&p.link.pc,open=!!(a&&OPEN.indexOf(a.state)>=0);
 if(p.link&&p.link.storage==='error')return storageWords(p.link)+(state==='wait'?' Wait before unplugging.':'');
 var stuck=!!pc&&(pc.state==='stopping'||pc.state==='error');
 if(state==='')return '';
 if(state==='out'){
  if(!open)return 'Not connected. Check the cable and the PC.';
  return stuck?'Not connected. The answer is saved.':'Not connected. The answer is saved and continues when the PC is ready again.';
 }
 if(state==='wait')return 'Copying the model to the PC. Wait before unplugging.';
 if(!open)return 'Connected. Safe to unplug.';
 if(stuck)return 'Connected. Safe to unplug: the answer is saved.';
 return 'Connected. Safe to unplug: the answer is saved and continues when the PC is ready again.';
}
function drawPane(name,p){
 var off=name==='gpu'&&!p.link,a=off?null:p.answer,isOpen=!!(a&&OPEN.indexOf(a.state)>=0);
 var pc=name==='gpu'&&p.link&&p.link.pc;
 var final=!!a&&['done','failed','cancelled'].indexOf(a.state)>=0;
 var here=pc&&(!a||(a.where?a.where===pc.label:!final));
 var where=here?[pc.label,pc.gpu&&pc.gpu.name].filter(Boolean).join(' · '):p.where;
 line(name+'-model',off?'':[p.model,where].filter(Boolean).join(' · '));
 var state=off?'GPU Link is off.':words(name,p);
 if(pc&&pc.state==='copying'&&pc.total>0&&pc.copied!=null&&state==='Copying the model')
  state+=': '+Math.min(100,Math.floor(pc.copied*100/pc.total))+'%';
 var speed=a&&a.tps!=null;
 var live=name==='phone'&&a&&a.state==='answering'&&p.live_speed!=null&&!speed;
 text(name+'-speed',speed?a.tps+' tokens per second':live?'about '+p.live_speed+' pieces per second':state);
 line(name+'-state',[speed||live?state:'',a&&a.tokens!=null?a.tokens+' tokens':''].filter(Boolean).join(' · '));
 if(name==='gpu'){G('gpu-disclosure').hidden=off;line('gpu-cable',cableWords(p));G('gpu-cable').dataset.cable=cableState(p)}
 var box=G(name+'-answer'),value=a?a.text:'',follow=box.scrollHeight-box.clientHeight-box.scrollTop<=24;
 var changed=box.dataset.answer!==(a?a.id:'');
 if(changed){follow=true;box.dataset.answer=a?a.id:''}
 if(box.textContent!==value){box.textContent=value;if(follow)box.scrollTop=box.scrollHeight}
 else if(changed)box.scrollTop=box.scrollHeight;
 box.hidden=off;
 G(name+'-pane').dataset.open=String(isOpen);G(name+'-pane').dataset.state=a?a.state:'idle';
 G(name+'-stop').hidden=!isOpen;
 G(name+'-stop').onclick=function(){send({action:'cancel',id:a.id})};
}
function line(id,value){text(id,value);G(id).hidden=!value}
function targets(){return (pane?[pane]:['phone','gpu']).filter(function(n){return n!=='gpu'||current&&current.gpu.link})}
function askingBusy(){return targets().some(function(n){var p=current&&current[n],a=p&&p.answer;
 return a&&OPEN.indexOf(a.state)>=0||n==='gpu'&&p&&p.link&&p.link.storage==='error'})}
function buttons(){
 var disabled=!current||!targets().length||askingBusy();G('ask').disabled=disabled;
 text('ask',pane==='gpu'?'Ask the GPU':pane==='phone'||!current.gpu.link?'Ask the phone model':'Ask both models');
 Array.from(G('presets').children).forEach(function(b){b.disabled=disabled});
}
function draw(s){
 if(s.locked){current=null;open=false;G('controls').hidden=true;G('enrol').hidden=true;
  ['phone','gpu'].forEach(function(n){text(n+'-answer','');line(n+'-state','');
   line(n+'-model','');text(n+'-speed','Unlock the phone to use AI mode.');G(n+'-stop').hidden=true});
  text('enrol-text','');line('gpu-cable','');
  G('allow').hidden=true;G('forget').hidden=true;G('reopen').hidden=true;return;
 }
 if(!s.phone||!s.gpu)return;
 if(s.boot===boot&&s.rev<rev)return;
 boot=s.boot;rev=s.rev;current=s;G('controls').hidden=false;G('allow').hidden=false;
 // Every field is drawn synchronously from s; the browser paints after this task.
 drawPane('phone',s.phone);drawPane('gpu',s.gpu);
 open=[s.phone,s.gpu].some(function(p){return p.answer&&OPEN.indexOf(p.answer.state)>=0});
 var link=s.gpu.link,pc=link&&link.pc,pending=link&&link.pending;
 G('enrol').hidden=!pending;G('reopen').hidden=!(link&&link.enrol==='closed');G('forget').hidden=!pc;
 text('enrol-text',pending?'Use the GPU of '+pending.label+'? Code '+pending.code.slice(0,3)+' '+pending.code.slice(3)+
  '. This PC will see what you ask in AI mode and will keep a copy of the model file.':'');
 var nextCode=pending?pending.code:null,active=document.activeElement;
 if(nextCode&&nextCode!==pendingCode&&!G('thumb').hidden&&!(active&&/^(INPUT|TEXTAREA)$/.test(active.tagName)))
  G('deny').focus({preventScroll:true});
 pendingCode=nextCode;
 var key=JSON.stringify(s.presets);if(key!==presetKey){presetKey=key;
  var presets=G('presets');presets.textContent='';
  s.presets.forEach(function(q){var b=document.createElement('button');b.type='button';b.textContent=q.label;
   b.onclick=function(){if(b.disabled)return;G('question').value=q.question;ask()};presets.appendChild(b)});
 }
 buttons();
}
function interval(){return open?100:2000}
function schedule(){
 clearTimeout(timer);timer=null;
 if(document.hidden||busy||stale)return;
 timer=setTimeout(request,Math.max(0,last+interval()-performance.now()));
}
function request(){
 timer=null;if(document.hidden||busy)return;
 busy=true;last=performance.now();var body=queue.shift();
 var controller=new AbortController(),timeout,timedOut=false;
 var deadline=new Promise(function(resolve,reject){timeout=setTimeout(function(){
  timedOut=true;controller.abort();reject(new Error('timeout'));
 },10000)});
 var response=Promise.resolve().then(function(){return fetch('/api/ai',{
  method:body?'POST':'GET',headers:Object.assign({'X-OPOS-Token':token},body?{'Content-Type':'application/json'}:{}),
  body:body?JSON.stringify(body):undefined,signal:controller.signal});
 }).then(function(r){return r.json().then(function(s){return {status:r.status,snapshot:s}})});
 Promise.race([response,deadline]).then(function(result){
   var r=result,s=result.snapshot;
   if(r.status===403&&s&&s.error==='forbidden'){
    stale=true;text('err','This page is out of date. Reload it, then try again.');return}
   if(r.status===403&&s&&s.error==='locked'){queue=[];draw({locked:true})}
   if(r.status!==200){text('err',s.error||'AI mode could not update.');return}
   text('err','');draw(s);
  }).catch(function(){text('err',timedOut?'The phone did not answer.':'AI mode could not update.');
  }).finally(function(){clearTimeout(timeout);busy=false;schedule()});
}
function send(body){if(document.hidden)return;queue.push(body);schedule()}
function ask(){
 if(!current||!targets().length)return;
 if(askingBusy()){text('err','Wait for this answer, or stop it.');return}
 send({action:'ask',question:G('question').value,panes:targets()});
}
G('ask').onclick=ask;
G('question').onkeydown=function(e){if(e.key==='Enter'){e.preventDefault();ask()}};
['allow','deny'].forEach(function(action){G(action).onclick=function(){send({action:action,code:pendingCode})}});
['reopen','forget'].forEach(function(action){G(action).onclick=function(){send({action:action})}});
document.addEventListener('visibilitychange',schedule);
schedule();
"""


def render(token: str, nonce: str) -> str:
    """Build a separate app document without changing the home page."""
    return appdoc.render("AI mode", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


APP = apps.register(
    apps.App(
        "ai",
        "AI mode",
        "/ai",
        DISCLOSURE,
        "yes, with a local model",
        "questions to the enrolled PC when the GPU pane is used",
        "phone model and optional PC GPU",
        "real",
        "ai",
        "OPTIONAL",
        0,
        render,
        snapshot,
        post,
    )
)
