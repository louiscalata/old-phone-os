# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-page setup wizard for Old Phone OS."""
from __future__ import annotations
import html
import re
from opos import secure
from opos.setup import jaryo_steps, notices_screen

_TEMPLATE: str = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Old Phone OS setup</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;background:#000;color:#e6e2d9;font-family:system-ui,-apple-system,sans-serif;line-height:1.5}
h1{font-family:Georgia,'Times New Roman',serif;font-weight:700;border-bottom:3px solid #e6e2d9;padding-bottom:8px}
#meter{position:fixed;top:0;left:0;width:100%;height:3px;background:#222}
#meter-fill{height:100%;width:0;background:#e6e2d9;transition:width .2s}
.wrap{max-width:420px;margin:24px auto;padding:16px}
.panel{background:#000;border:1px solid #e6e2d9;padding:16px;margin:12px 0}
.muted{color:#bbb}
button{min-height:48px;background:#000;color:#e6e2d9;border:1px solid #e6e2d9;padding:8px 14px}
button:active{background:#e6e2d9;color:#000}
button:disabled{opacity:.5}
input,select,textarea{font-size:16px;width:100%;background:#000;color:#e6e2d9;border:1px solid #e6e2d9;padding:10px}
input[type=checkbox],input[type=radio]{width:24px;height:24px;margin:0;padding:0;flex:none;accent-color:#e6e2d9}
.panel label{display:flex;align-items:center;gap:8px;min-height:44px}
:focus-visible{outline:3px solid #e6e2d9;outline-offset:3px}
.row{display:flex;gap:8px;justify-content:space-between;margin-top:12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
.pending{background:#8a8f98}
.running{background:#f2c14e}
.ok{background:#e6e2d9}
.failed{background:#e5484d}
.skipped{background:#444;opacity:.6}
a{color:#e6e2d9}
#error{color:#e5484d}
.ff-tiles{display:grid;grid-template-columns:24px 1fr;gap:8px;align-items:center;border:0;margin:12px 0 0;padding:0}
.ff-tiles legend{padding:0;margin-bottom:8px}
.ff-tiles label{border:1px solid #e6e2d9;padding:8px 12px;min-height:48px}
.ff-tiles input:checked+label{border:3px double #e6e2d9;font-weight:700}
.ferr{color:#e5484d;margin:4px 0 0}
</style>
<div id="meter" role="progressbar" aria-label="Setup progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="meter-fill"></div></div>
<main class="wrap">
<h1>Old Phone OS setup</h1>
<p id="dry" hidden class="panel muted">Dry run: service and tool installation is previewed. Personal choices are still saved.</p>
<p id="error" hidden role="alert"></p>
<p id="stepno" class="muted"></p>
<section class="step panel" id="s1">
<h2>Device</h2>
<p>Confirm your device profile.</p>
<p><span id="d-name" class="muted"></span> <span id="d-bat"></span></p>
<p id="d-stub" hidden>The device could not be identified; using a generic profile.</p>
<label for="profile">Profile</label>
<select id="profile" aria-label="Device profile"></select>
<fieldset id="ff-tiles" class="ff-tiles">
<legend>What kind of phone is this?</legend>
<input type="radio" id="ff-slab" name="form_factor" value="slab" checked><label for="ff-slab">Regular phone</label>
<input type="radio" id="ff-foldable-book" name="form_factor" value="foldable-book"><label for="ff-foldable-book">Foldable</label>
<input type="radio" id="ff-dual-screen" name="form_factor" value="dual-screen"><label for="ff-dual-screen">Two screens (Surface Duo)</label>
<input type="radio" id="ff-slide-keyboard" name="form_factor" value="slide-keyboard"><label for="ff-slide-keyboard">Slide-out keyboard</label>
</fieldset>
<div class="row"><span></span><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s2" hidden>
<h2>Services</h2>
<p>Choose what stays on.</p>
<label><input type="checkbox" id="svc-calls" value="calls" checked disabled> Calls</label>
<label><input type="checkbox" id="svc-sms" value="sms"> Messages</label>
<label><input type="checkbox" id="svc-alarms" value="alarms"> Alarms</label>
<label><input type="checkbox" id="svc-apps" value="apps"> Apps you use</label>
<div class="row"><button type="button" data-back>Back</button><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s3" hidden>
<h2>Daily goal</h2>
<p>Set when you want the battery to last until.</p>
<label><input type="checkbox" id="goal-enabled" checked> Aim to last until a time</label>
<label for="goal-time">Time</label>
<input type="time" id="goal-time" value="23:00" aria-label="Goal time" aria-describedby="goal-time-err">
<p id="goal-time-err" class="ferr" hidden></p>
<div class="row"><button type="button" data-back>Back</button><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s4" hidden>
<h2>Battery health</h2>
<p>Tell us about your battery.</p>
<label><input type="radio" name="battery_mode" value="auto" checked> Work it out for me</label>
<label><input type="radio" name="battery_mode" value="worn"> My battery is worn</label>
<label><input type="radio" name="battery_mode" value="normal"> My battery is fine</label>
<div class="row"><button type="button" data-back>Back</button><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s5" hidden>
<h2>Security</h2>
<p>A private session token is created for you. Your browser uses it to send setup requests.</p>
<label for="ssh-key">SSH public key (optional)</label>
<textarea id="ssh-key" rows="3" aria-label="SSH public key" aria-describedby="ssh-key-err"></textarea>
<p id="ssh-key-err" class="ferr" hidden></p>
<p class="muted">The key is saved for later use, with a command to enable it. Setup does not enable SSH or change which keys can sign in.</p>
<div class="row"><button type="button" data-back>Back</button><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s6" hidden>
<h2>AI coding tools</h2>
<p>Optional. Install terminal coding assistants on this device. Nothing is installed unless you tick one.</p>
<div id="ai-list"></div>
<fieldset id="ai-modes">
<label><input type="radio" name="ai_mode" value="lite" checked> Lite: runs only while charging, lightest install</label>
<label><input type="radio" name="ai_mode" value="full"> Full: also runs on battery above a safe level</label>
</fieldset>
<p id="ai-note" class="muted"></p>
<p id="ai-off" hidden></p>
<div class="row"><button type="button" data-back>Back</button><button type="button" data-next>Next</button></div>
</section>
<section class="step panel" id="s7" hidden>
<h2>Review and apply</h2>
<p>Check everything before applying.</p>
<p>Setup saves choices and installs a background service for your user account on macOS or Linux. It does not replace your operating system. No model weights are bundled. Optional AI coding tools are installed only if you selected them above.</p>
<ul id="summary"></ul>
<button type="button" id="apply">Apply</button>
<ul id="progress" aria-live="polite"></ul>
<p id="done" hidden>All set. <a id="open-link" href="#">Open Old Phone OS</a></p>
<p id="done-norun" hidden></p>
<p id="failed" hidden></p>
<p id="failed-help" hidden class="muted">Your choices are kept. Tap Try again. If it still fails, restart the device and run opos again.</p>
<button type="button" id="retry" hidden>Try again</button>
<div class="row"><button type="button" data-back>Back</button><span></span></div>
</section>
</main>
<script>
const q=s=>document.querySelector(s);
const qa=s=>[...document.querySelectorAll(s)];
const token=q('meta[name="opos-token"]').content;
const H=()=>({"X-OPOS-Token":token});
let S=null;
let cur=0;
const steps=qa(".step:not(.jaryo-step)");
const fill=q("#meter-fill");
const meter=q("#meter");
const err=q("#error");
function showErr(m){err.textContent=m||"";err.hidden=!m;}
async function errMsg(r){try{let j=await r.json();if(Array.isArray(j.errors)&&j.errors.length)return j.errors.join(", ");if(j.error)return j.error;return JSON.stringify(j);}catch(_){try{return await r.text();}catch(_){return r.statusText;}}}
function updMeter(){let p=Math.round((cur+1)/steps.length*100);fill.style.width=p+"%";meter.setAttribute("aria-valuenow",String(p));q("#stepno").textContent="Step "+(cur+1)+" of "+steps.length;}
function show(i){q("#jaryo-notices").hidden=true;cur=i;steps.forEach((e,k)=>e.hidden=k!==i);updMeter();if(i===6)buildSummary();var h=steps[i].querySelector("h2");if(h){h.tabIndex=-1;h.focus();}}
function fmtBat(v){return v==null||v===""?"unknown":Math.round(v*100)+"%";}
function curDev(){return window._curDev||(S&&S.device)||(S&&S.config&&S.config.device)||{id:"generic",name:"generic",source:"stub",battery_health:null};}
function renderDevice(d){q("#d-name").textContent=d.name||"unknown";q("#d-bat").textContent=fmtBat(d.battery_health);q("#d-stub").hidden=d.source!=="stub";}
function buildSummary(){let SVL={calls:"Calls",sms:"Messages",alarms:"Alarms",apps:"Apps you use"};let BML={auto:"Work it out for me",worn:"My battery is worn",normal:"My battery is fine"};let c=collect();let ul=q("#summary");ul.textContent="";let items=[["Device",c.device.name],["Services",c.services.map(x=>SVL[x]||x).join(", ")],["Goal",c.goal.enabled?c.goal.time:"Off"],["Battery",BML[c.battery_mode]||c.battery_mode],["SSH",c.security.ssh_public_key?"Added":"None"]];let at=c.ai_tools.tools.length?c.ai_tools.tools.join(", ")+" ("+c.ai_tools.mode+")":"None";items.push(["AI tools",at]);items.forEach(v=>{let li=document.createElement("li");li.textContent=v[0]+": "+v[1];ul.appendChild(li);});}
const FF=["slab","foldable-book","dual-screen","slide-keyboard"];
function getFF(){let r=q('input[name="form_factor"]:checked');return r&&FF.includes(r.value)?r.value:"slab";}
function setFF(v){let w=FF.includes(v)?v:"slab";qa('input[name="form_factor"]').forEach(e=>{e.checked=e.value===w;});}
function collect(){let svc=[];if(q("#svc-calls").checked)svc.push("calls");if(q("#svc-sms").checked)svc.push("sms");if(q("#svc-alarms").checked)svc.push("alarms");if(q("#svc-apps").checked)svc.push("apps");let bm=q('input[name="battery_mode"]:checked');let d=curDev();let at=[];qa(".ai-cb").forEach(e=>{if(e.checked)at.push(e.value)});let am=q('input[name="ai_mode"]:checked');let md=am?am.value:"lite";return {version:1,device:{id:d.id,name:d.name,source:d.source,battery_health:d.battery_health,form_factor:getFF()},services:svc,goal:{enabled:q("#goal-enabled").checked,time:q("#goal-time").value},battery_mode:bm?bm.value:"auto",security:{ssh_public_key:q("#ssh-key").value.trim()||null},ai_tools:{tools:at,mode:md},applied:false};}
function renderAi(){let list=q("#ai-list");let note=q("#ai-note");let off=q("#ai-off");if(!list||!note||!off)return;let ai=S&&S.ai_tools;if(!ai){note.textContent="";off.hidden=false;off.textContent="Not available on this system.";list.hidden=true;let fs=q("#ai-modes");if(fs)fs.hidden=true;return;}let p=ai.prefix||"";let f=ai.free_mb;let n=p;if(f!=null)n+=(n?" ":"")+f+" MB free";if(ai.volatile_warning)n+=(n?" ":"")+ai.volatile_warning;note.textContent=n;if(ai.supported===false||ai.error){list.hidden=true;list.textContent="";let fs=q("#ai-modes");if(fs)fs.hidden=true;off.hidden=false;off.textContent=ai.error?ai.error:"Not available on this system.";return;}off.hidden=true;off.textContent="";list.hidden=false;let fs=q("#ai-modes");if(fs)fs.hidden=false;list.textContent="";(ai.tools||[]).forEach(t=>{let wrap=document.createElement("div");let lab=document.createElement("label");let cb=document.createElement("input");cb.type="checkbox";cb.value=t.id;cb.className="ai-cb";if(t.ok===false)cb.disabled=true;lab.appendChild(cb);lab.appendChild(document.createTextNode(" "+(t.name||t.id)));wrap.appendChild(lab);let m=document.createElement("div");m.className="muted";if(t.ok===false)m.textContent=t.problem||"";else m.textContent="Sign in with: "+t.signin+" needs "+t.min_free_mb+" MB";wrap.appendChild(m);list.appendChild(wrap);});if(S.config&&!S.first_run&&S.config.ai_tools){let cfg=S.config.ai_tools;if(cfg.tools)cfg.tools.forEach(id=>{let c=qa(".ai-cb").find(e=>e.value===id);if(c&&!c.disabled)c.checked=true;});if(cfg.mode==='lite'||cfg.mode==='full'){let r=q('input[name="ai_mode"][value="'+cfg.mode+'"]');if(r)r.checked=true;}}}
function fillProfiles(){let sel=q("#profile");sel.textContent="";let opt=document.createElement("option");opt.value="";opt.textContent="Use detected";sel.appendChild(opt);(S.profiles||[]).forEach(p=>{let o=document.createElement("option");o.value=p.id||p.name;o.textContent=p.name||p.id;sel.appendChild(o);});}
async function load(){try{let r=await fetch("/setup/api/state",{headers:H()});if(!r.ok)throw new Error(await errMsg(r));S=await r.json();if(S.config_error)showErr("Your saved settings could not be read, so the recommended settings are shown. Check each step, then tap Apply.");if(S.dry_run)q("#dry").hidden=false;if(S.app_port)q("#open-link").href="http://127.0.0.1:"+S.app_port+"/";fillProfiles();let d=S.device||{};if(S.config&&S.config.device&&!S.first_run)d=S.config.device;window._curDev=d;renderDevice(d);setFF(S.config&&S.config.device&&!S.first_run?S.config.device.form_factor:"slab");if(S.config&&!S.first_run){let c=S.config;if(c.services){q("#svc-sms").checked=c.services.includes("sms");q("#svc-alarms").checked=c.services.includes("alarms");q("#svc-apps").checked=c.services.includes("apps");}if(c.goal){q("#goal-enabled").checked=!!c.goal.enabled;q("#goal-time").value=c.goal.time||"23:00";}if(c.battery_mode){let r2=q('input[name="battery_mode"][value="'+c.battery_mode+'"]');if(r2)r2.checked=true;}if(c.security)q("#ssh-key").value=c.security.ssh_public_key||"";}let sp=null;try{sp=sessionStorage.getItem("opos_setup_profile");}catch(_){}renderAi();if(sp&&[...q("#profile").options].some(o=>o.value===sp)){q("#profile").value=sp;changeProfile();}}catch(e){showErr(e.message);}}
// The select only keeps a profile once its device has loaded, so what is shown is what Apply sends.
// Next and Apply wait while a device loads; after 8 s the load counts as failed.
function busy(on){qa("[data-next],#apply,#retry").forEach(b=>b.disabled=on);}
function keepPid(v){window._pid=v;try{if(v)sessionStorage.setItem("opos_setup_profile",v);else sessionStorage.removeItem("opos_setup_profile");}catch(_){}}
async function changeProfile(){let sel=q("#profile"),v=sel.value,pv=window._pid||"",n=window._pn=(window._pn||0)+1;try{if(!v){keepPid("");window._curDev=S.device;renderDevice(window._curDev);return;}busy(true);let d=await Promise.race([(async()=>{let r=await fetch("/setup/api/device",{method:"POST",headers:{...H(),"Content-Type":"application/json"},body:JSON.stringify({profile_id:v})});if(!r.ok)throw new Error(await errMsg(r));return r.json();})(),new Promise((_,z)=>setTimeout(()=>z(new Error("The device did not load. Try again.")),8000))]);if(n!==window._pn||sel.value!==v)return;keepPid(v);window._curDev=d;renderDevice(window._curDev);}catch(e){if(n!==window._pn)return;if(sel.value===v){sel.value=pv;keepPid(pv);}showErr(e.message);}finally{if(n===window._pn)busy(false);}}
let pollId=null;
async function poll(){let ul=q("#progress");ul.textContent="";async function tick(){try{let r=await fetch("/setup/api/progress",{headers:H()});if(!r.ok)throw new Error(await errMsg(r));let j=await r.json();let rows=j.steps||j.items||[];ul.textContent="";let failed=null;rows.forEach(s=>{let li=document.createElement("li");let dot=document.createElement("span");dot.className="dot "+(s.status||"pending");let lab=document.createElement("span");lab.textContent=(s.label||s.name||"")+" ";let det=document.createElement("span");det.textContent=s.detail||s.message||"";det.className="muted";li.appendChild(dot);li.appendChild(lab);li.appendChild(det);ul.appendChild(li);if(s.status==="failed")failed=s;});if(j.done||j.finished){clearInterval(pollId);if(j.ok===false||failed){q("#failed").textContent=failed?failed.detail||failed.message||"Failed":"Setup failed";q("#failed").hidden=false;q("#retry").hidden=false;q("#failed-help").hidden=false;}else{let hr=rows.find(s=>s.id==="health");if(hr&&hr.status==="skipped"){let msg=S&&S.dry_run?"Dry run finished. No services or tools were installed. Personal choices are still saved.":"Your choices are saved, but Old Phone OS cannot start on its own here. See the README, section Quickstart, to start it by hand.";q("#done-norun").textContent=msg;q("#done-norun").hidden=false;}else{q("#done").hidden=false;}}} }catch(e){showErr(e.message);}};pollId=setInterval(tick,700);tick();}
async function doApply(){showErr("");q("#failed").hidden=true;q("#retry").hidden=true;q("#failed-help").hidden=true;q("#done").hidden=true;q("#done-norun").hidden=true;let cfg=collect();try{let r=await fetch("/setup/api/apply",{method:"POST",headers:{...H(),"Content-Type":"application/json"},body:JSON.stringify({config:cfg})});if(!r.ok)throw new Error(await errMsg(r));q("#apply").hidden=true;poll();try{if(window.jaryoSetup)window.jaryoSetup.start();}catch(_){}}catch(e){showErr(e.message);}}
q("#notices-continue").addEventListener("click",function(e){e.preventDefault();q("#jaryo-notices").hidden=true;q("#profile").focus();});
q("#profile").addEventListener("change",changeProfile);
// Steps with a typed field are checked by the server before moving on; the reason shows under the field. If the check cannot run or takes over 4 s, Next still works.
const FIELDS={2:["goal-time",/^goal/,"Enter a time like 23:00."],4:["ssh-key",/ssh_public_key/,"This is not an SSH public key. Clear the box or paste the whole key line."]};
function fieldErr(id,m){let f=q("#"+id),p=q("#"+id+"-err");if(!f||!p)return;p.textContent=m||"";p.hidden=!m;if(m)f.setAttribute("aria-invalid","true");else f.removeAttribute("aria-invalid");}
async function check(F){let r=await fetch("/setup/api/validate",{method:"POST",headers:{...H(),"Content-Type":"application/json"},body:JSON.stringify({config:collect()})});if(!r.ok)return null;let j=await r.json();return (j.errors||[]).find(e=>F[1].test(e))||null;}
async function next(){let i=cur,F=FIELDS[i];if(F){let bad=null;try{bad=await Promise.race([check(F),new Promise(z=>setTimeout(()=>z(null),4000))]);}catch(_){}if(cur!==i)return;fieldErr(F[0],bad?F[2]:"");if(bad){q("#"+F[0]).focus();return;}}if(cur===i)show(i+1);}
Object.values(FIELDS).forEach(F=>{let f=q("#"+F[0]);if(f)f.addEventListener("input",()=>fieldErr(F[0],""));});
qa("[data-next]").forEach(b=>b.addEventListener("click",next));
qa("[data-back]").forEach(b=>b.addEventListener("click",()=>show(cur-1)));
q("#apply").addEventListener("click",doApply);
q("#retry").addEventListener("click",doApply);
load();updMeter();
</script>
"""

# Personal (Jaryo) steps: client runtime for the fragment from jaryo_steps.fragment().
# Saved choices the page cannot show are treated as not chosen yet, so the user lands on that
# step instead of every panel staying hidden. The summary is only shown after
# /setup/api/prefs/finish succeeds; until then nothing claims the choices are applied.
JARYO_SCRIPT = r"""
try { (function () {
  const root = document.getElementById('jaryo-setup');
  if (!root) return;
  const panels = Array.from(root.querySelectorAll('[data-jstep]'));
  const names = panels.map(e => e.dataset.jstep);
  const summary = document.getElementById('jaryo-summary');
  const summaryBack = document.getElementById('jaryo-summary-back');
  const end = document.getElementById('jaryo-disclaimer-end');
  const error = document.getElementById('jaryo-error');
  const retry = document.getElementById('jaryo-retry');
  // JRY32: the leading "Choose your edition" panel is not a data-jstep. An edition is the existing
  // device_type answer, so the personal steps, resume, finish and skip-all below are unchanged.
  const edition = document.getElementById('jaryo-edition');
  const useEdition = document.getElementById('jaryo-use-edition');
  const customise = document.getElementById('jaryo-customise');
  let prepared = false, editionBase;
  const WIFI_LATER = 'Set up later in Settings › Network';
  const PIN_LATER = 'Set up later in Settings › Device';
  const TILE_DEFAULTS = {phone: 'medium', messages: 'medium', helper: 'small', settings: 'small'};
  const TILE_LABELS = {small: 'Small', medium: 'Medium', wide: 'Wide'};
  const LOAD_FAILED = 'Your choices could not be loaded or saved. Check that setup is still running, then try loading choices again.';
  const FINISH_FAILED = 'Your choices are saved but have not been applied yet. Check that setup is still running, then try finishing again.';
  const RECOVERED = 'Some saved choices could not be read, so those steps count as not chosen yet. Choose an option or use Skip on each step shown.';
  // answers and skipped hold only entries this page could verify. pending is the first step that still
  // needs an answer or a skip; unreadable is set when the last server reply held entries that were dropped.
  let answers = {}, skipped = [], pending = null, unreadable = false, applied = false, busy = false, retryAction = null;
  function showAlert(message, action, label) {
    error.textContent = message || '';
    error.hidden = !message;
    retryAction = message && action ? action : null;
    retry.hidden = !retryAction;
    if (retryAction) { retry.textContent = label; retry.focus(); }
  }
  function notice() { if (unreadable) showAlert(RECOVERED); }
  async function request(url, body) {
    const headers = {'X-OPOS-Token': document.querySelector('meta[name="opos-token"]').content};
    const options = {headers};
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      options.method = 'POST'; options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (!response.ok) throw new Error('request');
    return response.json();
  }
  // Every step has an h3 (inside the legend for choice steps), so heading navigation and focus agree.
  function heading(panel) { return panel.querySelector('h3') || panel.querySelector('legend'); }
  function hideAll() {
    panels.forEach(p => { p.hidden = true; });
    if (edition) edition.hidden = true;
    summary.hidden = true; summaryBack.hidden = true; end.hidden = true;
  }
  function show(i) {
    hideAll();
    panels[i].hidden = false;
    window.jaryoSetup.stepIndex = i;
    const h = heading(panels[i]);
    if (h) h.focus();
  }
  function editionInputs() { return edition ? Array.from(edition.querySelectorAll('input')) : []; }
  function pickedEdition() { return editionInputs().find(e => e.checked) || null; }
  // Same heuristic as the kiosk's Settings "Auto looks like" line (phone-b.js); the server exposes no form factor.
  function detectEdition() {
    try {
      const mq = q => window.matchMedia(q).matches;
      if (mq('(min-width:600px) and (min-aspect-ratio:3/4) and (max-aspect-ratio:4/3)')) return 'foldable';
      if (mq('(orientation:landscape) and (max-height:520px)')) return 'slideout';
    } catch (_) {}
    return 'slab';
  }
  function useLabel() {
    const picked = pickedEdition();
    if (useEdition) useEdition.textContent = 'Use ' + (picked ? picked.dataset.jname : 'edition');
  }
  function prepareEdition() {
    const detected = detectEdition(), saved = answers.device_type, inputs = editionInputs();
    const pick = inputs.some(e => e.value === saved) ? saved : detected;
    inputs.forEach(input => {
      input.checked = input.value === pick;
      const kicker = input.parentElement.querySelector('[data-jkicker]');
      if (kicker) kicker.textContent = input.value === detected ? 'Suggested' : kicker.dataset.jkicker;
    });
    useLabel();
    prepared = true; editionBase = saved;
  }
  function showEdition() {
    // Back from the steps: a device type saved at j4 since the panel was prepared is what Use must offer, so the
    // panel is prepared again from it. Otherwise an edition picked here but not yet used is kept.
    if (!prepared || answers.device_type !== editionBase) prepareEdition();
    hideAll();
    edition.hidden = false;
    window.jaryoSetup.stepIndex = 0;
    const h = heading(edition);
    if (h) h.focus();
  }
  function inputs(name) { return Array.from(panels[names.indexOf(name)].querySelectorAll('input')); }
  function label(name, value) {
    const match = inputs(name).find(e => e.value === value);
    return match ? match.parentElement.textContent.trim() : value === 'allapps' ? 'All apps' : 'Saved choice';
  }
  function layoutSizes(value) {
    const saved = value && value.sizes, sizes = {};
    Object.keys(TILE_DEFAULTS).forEach(id => {
      const size = saved && saved[id];
      sizes[id] = typeof size === 'string' && Object.prototype.hasOwnProperty.call(TILE_LABELS, size) ? size : TILE_DEFAULTS[id];
    });
    return sizes;
  }
  function restoreSizes(value) {
    const sizes = layoutSizes(value);
    Object.keys(TILE_DEFAULTS).forEach(id => {
      const select = document.getElementById('jaryo-size-' + id);
      if (select) select.value = sizes[id];
    });
  }
  function collectLayout(selected) {
    const sizes = {};
    const saved = answers.layout && answers.layout.sizes;
    // The schema also accepts an All apps size; keep a valid saved value even
    // though this step edits only the four app tiles.
    if (saved && Object.prototype.hasOwnProperty.call(saved, 'allapps') &&
        typeof saved.allapps === 'string' && Object.prototype.hasOwnProperty.call(TILE_LABELS, saved.allapps)) sizes.allapps = saved.allapps;
    Object.keys(TILE_DEFAULTS).forEach(id => {
      const select = document.getElementById('jaryo-size-' + id);
      if (!select || typeof select.value !== 'string' || !Object.prototype.hasOwnProperty.call(TILE_LABELS, select.value)) throw new Error('tile size');
      sizes[id] = select.value;
    });
    return {favourites: selected.map(e => e.value), sizes};
  }
  function sizeSummary(value) {
    const sizes = layoutSizes(value);
    return 'Starting tile sizes: ' + Object.keys(TILE_DEFAULTS).map(id => label('layout', id) + ': ' + TILE_LABELS[sizes[id]]).join(', ');
  }
  function wifiText(value) {
    if (value && typeof value === 'object') return 'Network name "' + value.ssid + '" saved as a preference only, not connected. Join it later in Settings › Network';
    return WIFI_LATER;
  }
  function complete() {
    hideAll();
    const lines = names.map((name, i) => {
      const value = answers[name];
      let text = skipped.includes(name) ? 'Skipped (recommended settings)' : 'Recommended settings';
      if (!skipped.includes(name) && value !== undefined) {
        if (name === 'layout') text = (value.favourites || []).map(v => label(name, v)).join(', ') || 'No favourites';
        else if (name === 'wifi') text = wifiText(value);
        else if (name === 'pin') text = PIN_LATER;
        else text = label(name, value);
      }
      if (name === 'wifi' && skipped.includes(name)) text += '; ' + WIFI_LATER;
      if (name === 'pin' && skipped.includes(name)) text += '; ' + PIN_LATER;
      if (name === 'layout') text += '; Favourite choices are recorded only. Home app visibility and order are unchanged. ' + sizeSummary(value);
      return heading(panels[i]).textContent + ': ' + text;
    });
    lines.unshift(applied ? 'Your choices are saved and applied.' : 'Your choices are saved but not applied yet.');
    summary.textContent = lines.join('\n');
    summary.hidden = false; summaryBack.hidden = false; end.hidden = false; summary.focus();
  }
  function valid(name, value) {
    const options = inputs(name);
    if (name === 'layout') {
      if (!value || typeof value !== 'object' || Array.isArray(value) || !Array.isArray(value.favourites) || value.favourites.length > 8 ||
          Object.keys(value).some(key => key !== 'favourites' && key !== 'sizes') ||
          !value.favourites.every(v => v === 'allapps' || options.some(input => input.value === v))) return false;
      if (value.sizes == null) return true; // Older saved choices have no size map.
      return typeof value.sizes === 'object' && !Array.isArray(value.sizes) && Object.keys(value.sizes).every(id =>
        (id === 'allapps' || Object.prototype.hasOwnProperty.call(TILE_DEFAULTS, id)) &&
        typeof value.sizes[id] === 'string' &&
        Object.prototype.hasOwnProperty.call(TILE_LABELS, value.sizes[id]));
    }
    if (name === 'wifi') return value === 'later' || value === 'skip' || (!!value && typeof value === 'object' && typeof value.ssid === 'string' && value.ssid.length > 0);
    // JRY34: record-only; never accept or display saved PIN digits.
    if (name === 'pin') return value === 'later' || value === 'skip';
    return options.some(input => input.value === value);
  }
  // Adopt a {state, resume} reply. Returns the step to show, or null when nothing is left to answer.
  // Saved choices determine the first unfinished step; a stale or missing resume marker
  // cannot skip unanswered steps or confirm completion.
  function adopt(data) {
    let problems = 0;
    let state = data && data.state;
    if (!state || typeof state !== 'object') { problems++; state = {}; }
    let saved = state.answers;
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) { problems++; saved = {}; }
    answers = {};
    Object.keys(saved).forEach(name => {
      if (names.includes(name) && valid(name, saved[name])) answers[name] = saved[name]; else problems++;
    });
    let list = state.skipped;
    if (!Array.isArray(list)) { problems++; list = []; }
    if (list.some(name => !names.includes(name) || name in answers)) problems++;
    skipped = list.filter(name => names.includes(name) && !(name in answers));
    root.dataset.theme = answers.theme || 'auto';
    panels.forEach(p => {
      const name = p.dataset.jstep, value = answers[name];
      p.querySelectorAll('input').forEach(input => {
        input.checked = name === 'layout' ? !!(value && (value.favourites || []).includes(input.value)) : input.value === value;
      });
    });
    restoreSizes(answers.layout);
    pending = names.find(name => !(name in answers) && !skipped.includes(name)) || null;
    const resume = data && data.resume;
    if (resume !== pending && !(pending === null && resume === 'done')) problems++;
    unreadable = problems > 0;
    applied = state.applied === true && pending === null && !unreadable;
    return pending;
  }
  async function read() { return adopt(await request('/setup/api/prefs')); }
  // A finish reply confirms the choices only as the standard {state, resume, effective} envelope with
  // state.applied exactly true, a completed resume and entries this page can read. Anything else,
  // including an empty body or a missing flag, is not a confirmation.
  function confirmed(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
    const state = data.state;
    if (!state || typeof state !== 'object' || Array.isArray(state)) return false;
    const saved = state.answers, list = state.skipped;
    if (!saved || typeof saved !== 'object' || Array.isArray(saved) || !Array.isArray(list)) return false;
    if (state.applied !== true) return false;
    if (!(data.resume === null || data.resume === 'done')) return false;
    if (!data.effective || typeof data.effective !== 'object' || Array.isArray(data.effective)) return false;
    if (Object.keys(saved).some(name => !names.includes(name) || !valid(name, saved[name]))) return false;
    return list.every(name => names.includes(name) && !(name in saved)) &&
      names.every(name => name in saved || list.includes(name));
  }
  // The only place that may claim the choices are applied, and only after the server confirmed it.
  // The confirmed state is adopted so the summary shows what the server saved. On a failed request or
  // an unconfirmed reply the current step (or the last one, if none is open) stays visible with an
  // alert and a retry that resends only the finish call.
  async function finish() {
    let data = null;
    try { data = await request('/setup/api/prefs/finish', {}); } catch (_) { data = null; }
    if (!confirmed(data)) {
      if (panels.every(p => p.hidden)) show(names.length - 1);
      showAlert(FINISH_FAILED, () => run(finish), 'Try finishing again');
      return;
    }
    adopt(data); applied = true; complete();
  }
  async function run(action) {
    if (busy) return;
    busy = true; showAlert('');
    try { await action(); }
    catch (_) { showAlert(LOAD_FAILED, start, 'Try loading choices again'); }
    finally { busy = false; }
  }
  async function start() {
    root.hidden = false;
    await run(async () => {
      const next = await read();
      const device = typeof curDev === 'function' ? curDev() : {};
      const radios = device && Array.isArray(device.radios_present) ? device.radios_present : [];
      document.getElementById('jaryo-radio').textContent = 'Wi-Fi radio: ' + (radios.includes('wifi') ? 'present' : 'not detected');
      notice();
      // A readable, untouched fresh install leads with the edition; anything answered, skipped or
      // unreadable resumes at its step so the recovery notice points at a step with controls.
      if (next == null) { if (applied) complete(); else await finish(); }
      else if (edition && !unreadable && next === names[0] && !Object.keys(answers).length && !skipped.length) { prepareEdition(); showEdition(); }
      else show(names.indexOf(next));
    });
  }
  async function save(skip) {
    await run(async () => {
      const i = window.jaryoSetup.stepIndex, name = names[i], panel = panels[i];
      const selected = Array.from(panel.querySelectorAll('input')).filter(e => e.checked);
      if (!skip && name !== 'wifi' && name !== 'layout' && name !== 'pin' && !selected.length) {
        showAlert('Choose an option above, or use Skip to keep the recommended setting.');
        const input = panel.querySelector('input'); if (input) input.focus();
        return;
      }
      // Wi-Fi and PIN controls record a skip, never credentials.
      if (skip || name === 'wifi' || name === 'pin') await request('/setup/api/prefs/skip', {step: name});
      else await request('/setup/api/prefs', {step: name, value: name === 'layout' ? collectLayout(selected) : selected[0].value});
      await read();
      if (i + 1 < names.length) show(i + 1);
      else if (pending != null) { notice(); show(names.indexOf(pending)); }
      else await finish();
    });
  }
  root.querySelectorAll('[data-jnext]').forEach(b => { b.onclick = () => save(false); });
  root.querySelectorAll('[data-jskip], [data-jwifi], [data-jpin]').forEach(b => { b.onclick = () => save(true); });
  root.querySelectorAll('[data-jback]').forEach(b => {
    b.onclick = () => {
      if (busy) return;
      showAlert('');
      const i = window.jaryoSetup.stepIndex;
      if (i === 0 && edition) showEdition(); else show(Math.max(0, i - 1));
    };
  });
  // After skip-all the server state is read back; an unreadable entry shows its step, otherwise finish applies.
  async function skipRest() {
    await request('/setup/api/prefs/skip-all', {});
    await read(); notice();
    if (pending != null) show(names.indexOf(pending)); else await finish();
  }
  document.getElementById('jaryo-skip-all').onclick = () => run(skipRest);
  // Use <edition>: save the edition as device_type, record the recommended settings for the rest, then finish.
  if (useEdition) useEdition.onclick = () => run(async () => {
    const picked = pickedEdition();
    if (!picked) { showAlert('Choose an edition above, or tap Customise instead.'); return; }
    await request('/setup/api/prefs', {step: 'device_type', value: picked.value});
    await skipRest();
  });
  if (customise) customise.onclick = () => { if (!busy) { showAlert(''); show(0); } };
  summaryBack.onclick = () => { if (!busy) { showAlert(''); show(names.length - 1); } };
  retry.onclick = () => retryAction ? retryAction() : undefined;
  root.addEventListener('change', e => {
    if (e.target.name === 'theme') root.dataset.theme = e.target.value;
    if (e.target.name === 'edition') useLabel();
  });
  window.jaryoSetup = {stepIndex: 0, start};
})(); } catch (_) {}
"""

_JARYO_STYLE = "\n#jaryo-setup legend h3{margin:0;font:inherit}"
_LEGEND = re.compile(r'<legend tabindex="-1">(.*?)</legend>')


def _with_headings(fragment: str) -> str:
    """Keep every personal step reachable by heading navigation.

    Choice steps are fieldsets named by a legend. HTML lets a legend contain a heading, so the
    legend wraps an h3: the group keeps its name and the step keeps a real heading to focus.
    """
    return _LEGEND.sub(lambda m: '<legend><h3 tabindex="-1">' + m.group(1) + '</h3></legend>', fragment)


def page(token: str, nonce: str) -> str:
    """Return full HTML with token meta and nonced script/style."""
    esc: str = html.escape(token, quote=True)
    h: str = _TEMPLATE.replace("<title>Old Phone OS setup</title>", f'<meta name="opos-token" content="{esc}"><title>Old Phone OS setup</title>')
    # JRY33: the install notices come first, before step s1; they need no script.
    h = h.replace('<section class="step panel" id="s1">', notices_screen.fragment() + '<section class="step panel" id="s1">', 1)
    h = h.replace('</main>', _with_headings(jaryo_steps.fragment()) + '\n</main>', 1)
    h = h.replace('</style>', notices_screen.STYLE + jaryo_steps.STYLE + _JARYO_STYLE + '\n</style>', 1)
    # JARYO_SCRIPT is the one personal-steps runtime (JRY22 recovery, JRY32 edition and JRY34 PIN).
    h = h.replace('load();updMeter();\n</script>', JARYO_SCRIPT + '\nload();updMeter();\n</script>', 1)
    return secure.add_nonce(h, nonce)
