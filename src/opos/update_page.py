# SPDX-License-Identifier: GPL-3.0-or-later
"""GET /update: the software-updates page. A separate small page, because the main page is at its size budget.

It looks like the rest of OPOS because it inlines web_static/jaryo.css (the colour and type tokens, buttons, focus
ring, light and dark). The facts are rendered here, escaped, so the page reads without its script; the script only
adds the buttons and a live refresh from /api/update. Text size and theme follow the main page's saved choices.

The main page reaches it with one link in Settings, About ("Updates"); a full-page navigation, and "Back to the phone" returns.
"""
import json
from html import escape
from pathlib import Path

from opos import secure, update_client

_STATIC = Path(__file__).with_name("web_static")

_CSS = """
html[data-text="s"]{font-size:87.5%}html[data-text="l"]{font-size:125%}html[data-text="xl"]{font-size:150%}
body{margin:0;min-height:100vh}
main{max-width:640px;margin:0 auto;padding:16px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-bottom:max(16px,env(safe-area-inset-bottom,0px))}
[hidden]{display:none!important}
a{color:var(--jaryo-fg);display:inline-block;min-height:48px;line-height:48px}
h1{margin:8px 0}
section{border-top:1px solid var(--jaryo-fg);padding:12px 0}
h2{margin:0 0 8px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}
dt{font-family:var(--jaryo-cond);text-transform:uppercase;letter-spacing:.04em}
dd{margin:0;overflow-wrap:anywhere}
p{margin:8px 0;overflow-wrap:anywhere}
button{font:inherit;border:1px solid var(--jaryo-fg);padding:0 16px;margin:4px 8px 4px 0}
button:disabled{cursor:not-allowed}
progress{display:block;width:100%;height:16px;accent-color:var(--jaryo-fg)}
@media(max-width:360px){dl{grid-template-columns:1fr}}
"""

_JS = """
(function(){
var ONCE=@@ONCE@@,G=function(i){return document.getElementById(i)},D=document.documentElement,tok='',until=0,timer=null,armed=false,armT=null,last=null,where='u-note',tn=false;
try{tok=document.querySelector('meta[name=opos-token]').content}catch(e){}
try{var t=localStorage.getItem('jaryo_theme');if(t==='light'||t==='dark')D.dataset.theme=t;
var x=localStorage.getItem('opos_text');if(x==='s'||x==='l'||x==='xl')D.dataset.text=x}catch(e){}
function T(i,v){var e=G(i);if(e)e.textContent=v}
function H(i,h){var e=G(i);if(e)e.hidden=!!h}
function mb(n){return(n/1048576).toFixed(1)+' MB'}
function call(u,o){return fetch(u,o).then(function(r){return r.json().then(function(j){return{s:r.status,j:j}},function(){return{s:r.status,j:{error:'OPOS sent a reply this page cannot read.'}}})})}
function post(a){return call('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':tok},body:JSON.stringify({action:a})})}
function load(){return call('/api/update',{headers:{'X-OPOS-Token':tok}})}
function msg(r){if(r.s===403){H('u-stale',false);return'This page is out of date. Reload it, then try again.'}
if(r.s===429)return'Too many requests in a row. Wait a few seconds, then try again.';
return r.j&&r.j.error?r.j.error:'Something went wrong in OPOS. Try again in a minute.'}
function settled(s){var j=s.job||{},p=s.pending||[],st=s.status,w=/^(verifying|applying|checking)$/.test(st?st.phase:'');
if(j.phase==='downloading'||p.length||w||(s.rollback&&s.rollback.requested))return false;
if(j.phase==='done')return !!(st&&st.serial===j.serial&&/^(done|rolled-back|failed)$/.test(st.phase));
return true}
function show(s){last=s;H('u-na',s.available);H('u-live',!s.available);
if(!s.available){T('u-na',s.message||'Updates are not available on this install.');return}
var r=s.running||{},o=s.source||{},c=s.check,j=s.job||{},p=s.pending||[],st=s.status,rb=s.rollback||{},busy=j.phase==='downloading';
T('u-ver',r.version||'unknown');T('u-rev',r.revision?r.revision.slice(0,12):'unknown');T('u-ser',r.serial==null?'unknown':String(r.serial));
T('u-src',o.configured?o.url:(o.error||'Not set. OPOS contacts no server for updates.'));
T('u-auto',o.configured?(o.auto_check?'On: one check every 6 hours':'Off'):'');
T('u-result',c?c.message+(c.time?' Checked '+c.time+'.':''):'');
G('u-check').disabled=busy||!o.configured;
H('u-go',!(o.configured&&c&&c.newer));G('u-go').disabled=busy||p.length>0;
H('u-prog',!busy);H('u-prog-t',!busy);
if(busy){var pr=G('u-prog');pr.max=j.total||100;pr.value=j.total?j.bytes:0;if(!j.total)pr.removeAttribute('value');T('u-prog-t',mb(j.bytes)+(j.total?' of '+mb(j.total):''))}
T('u-job',busy?'':(j.phase==='failed'||j.phase==='done'?j.message:'')+(p.length?' Waiting to be applied: '+p.join(', ')+'.':''));
T('u-phase',st?st.phase:'none yet');T('u-msg',st?st.message:'No update has run on this phone yet.');H('u-stall',!s.stalled);
var rq=G('u-rb');rq.disabled=!rb.possible||!!rb.requested;
if(!armed)T('u-rb',rb.requested?'Rollback requested':'Request rollback');
T('u-rb-why',rb.requested?'Waiting for the updater.':rb.possible?'':rb.once?ONCE:'There is no earlier version to go back to.')}
function N(v){T('u-note','');T('u-rb-note','');T(where,v)}
function tick(){load().then(function(r){if(r.s===200){if(tn){N('');tn=false}show(r.j);if(!settled(r.j)&&Date.now()<until)again(1000)}
else{N(msg(r));tn=true}},function(){N('OPOS is not answering. It may be restarting to finish an update. Trying again.');tn=true;if(Date.now()<until)again(3000)})}
function again(ms){clearTimeout(timer);timer=setTimeout(tick,ms)}
function unlock(){if(last)show(last);else G('u-check').disabled=false}
function go(a,m,w){where=w;N(m);G('u-check').disabled=true;
post(a).then(function(r){if(r.s===200){N('');show(r.j);until=Date.now()+600000;if(!settled(r.j))again(1000)}
else{N(msg(r));unlock()}},function(){N('OPOS is not responding. Wait a few seconds, reload this page, then try again.');unlock()})}
G('u-check').onclick=function(){go('update_check','Checking...','u-note')};
G('u-go').onclick=function(){go('update_download','Starting the download...','u-note')};
G('u-rb').onclick=function(){if(!armed){armed=true;T('u-rb','Press again to confirm');where='u-rb-note';N('This asks the updater to go back to the earlier version. OPOS restarts its server.');
clearTimeout(armT);armT=setTimeout(function(){armed=false;N('');if(last)show(last)},8000);return}
armed=false;clearTimeout(armT);go('update_rollback','Asking for a rollback...','u-rb-note')};
G('u-reload').onclick=function(){location.reload()};
until=Date.now()+600000;tick();
})();
"""


def _e(v):
    return escape("" if v is None else str(v), quote=True)


def page(token="", nonce="", snap=None):
    """The page for one snapshot (UpdateClient.snapshot()). Every value is escaped; nothing is fetched."""
    s = snap or {}
    avail = bool(s.get("available"))
    run, src, chk, st = s.get("running") or {}, s.get("source") or {}, s.get("check"), s.get("status")
    job, rb, pend = s.get("job") or {}, s.get("rollback") or {}, s.get("pending") or []
    rev = run.get("revision")
    busy = job.get("phase") == "downloading"
    if src.get("configured"):
        source = _e(src.get("url"))
        auto = "On: one check every 6 hours" if src.get("auto_check") else "Off"
    else:
        source = _e(src.get("error") or "Not set. OPOS contacts no server for updates.")
        auto = ""
    if chk:
        result = _e(chk.get("message")) + (f" Checked {_e(chk.get('time'))}." if chk.get("time") else "")
    else:
        result = ""
    newer = bool(src.get("configured") and chk and chk.get("newer"))
    if rb.get("requested"):
        rb_label, rb_why = "Rollback requested", "Waiting for the updater."
    else:
        rb_label = "Request rollback"
        rb_why = "" if rb.get("possible") else update_client.ONCE_ONLY if rb.get("once") else "There is no earlier version to go back to."
    job_text = _e(job.get("message") if job.get("phase") in ("failed", "done") else "")
    if pend:
        job_text += _e(f" Waiting to be applied: {', '.join(pend)}.")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="opos-token" content="{_e(token)}"><title>Software updates - Old Phone OS Interface</title><link rel="icon" href="/favicon.ico" type="image/png"><style>
{(_STATIC / "jaryo.css").read_text(encoding="utf-8")}
{_CSS}</style></head><body><main>
<p><a id="u-back" href="/">Back to the phone</a></p>
<h1 class="jaryo-head">Software updates</h1>
<div id="u-stale" role="alert" hidden><p>This page is out of date.</p><p><button type="button" id="u-reload">Reload this page</button></p></div>
<p id="u-na" role="status"{"" if not avail else " hidden"}>Updates are not available on this install.</p>
<div id="u-live"{"" if avail else " hidden"}>
<section aria-labelledby="u-h-run"><h2 id="u-h-run">Running now</h2>
<dl><dt>Version</dt><dd id="u-ver">{_e(run.get("version") or "unknown")}</dd>
<dt>Revision</dt><dd id="u-rev">{_e(rev[:12] if rev else "unknown")}</dd>
<dt>Serial</dt><dd id="u-ser">{_e("unknown" if run.get("serial") is None else run.get("serial"))}</dd></dl></section>
<section aria-labelledby="u-h-src"><h2 id="u-h-src">Update source</h2>
<p id="u-src">{source}</p><p>Automatic checks: <span id="u-auto">{auto}</span></p></section>
<section aria-labelledby="u-h-chk"><h2 id="u-h-chk">Check for updates</h2>
<p><button type="button" id="u-check"{"" if src.get("configured") and not busy else " disabled"}>Check for updates</button></p>
<p id="u-note" role="status" aria-live="polite"></p>
<p id="u-result" role="status" aria-live="polite">{result}</p>
<p><button type="button" id="u-go"{"" if newer else " hidden"}{" disabled" if busy or pend else ""}>Download and apply</button></p>
<progress id="u-prog" max="100" aria-label="Download progress" hidden></progress><p id="u-prog-t" role="status" hidden></p>
<p id="u-job" role="status" aria-live="polite">{job_text}</p></section>
<section aria-labelledby="u-h-last"><h2 id="u-h-last">Last update</h2>
<p>Phase: <span id="u-phase">{_e(st["phase"] if st else "none yet")}</span></p>
<p id="u-msg" role="status" aria-live="polite">{_e(st["message"] if st else "No update has run on this phone yet.")}</p>
<p id="u-stall" role="alert"{"" if s.get("stalled") else " hidden"}>The updater has not picked up what was delivered for several minutes, so it may not be running. Restart the phone to start it again.</p></section>
<section aria-labelledby="u-h-rb"><h2 id="u-h-rb">Go back</h2>
<p>Asks the updater to return to the earlier version. The OPOS server restarts.</p>
<p><button type="button" id="u-rb"{"" if rb.get("possible") and not rb.get("requested") else " disabled"}>{rb_label}</button></p>
<p id="u-rb-why">{rb_why}</p><p id="u-rb-note" role="status" aria-live="polite"></p></section>
</div>
<noscript><p>The buttons on this page need JavaScript. The facts above are current as of loading.</p></noscript>
</main><script>{_JS.replace("@@ONCE@@", json.dumps(update_client.ONCE_ONLY))}</script></body></html>"""
    return secure.add_nonce(html, nonce) if nonce else html
