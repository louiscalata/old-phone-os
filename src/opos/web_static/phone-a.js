// phone-a.js: phone-shell hook point (UXP00)
var G=i=>document.getElementById(i),E=t=>document.createElement(t);
var demoTime=G('demo')&&!G('demo').hidden?'Demo time —':'';
try{(function(){
var sheet=G('sheet'),timer;
if(!sheet||!window.opos)return;
function clear(){try{sheet.style.willChange='';clearTimeout(timer)}catch(e){}}
function moving(fn){return function(){
try{clearTimeout(timer);sheet.style.willChange='transform';timer=setTimeout(clear,400)}catch(e){}
return fn.apply(this,arguments);
}}
sheet.addEventListener('transitionend',function(e){
if(e.target===sheet&&e.propertyName==='transform')clear();
});
window.opos.open=window.openSheet=moving(window.openSheet);
window.opos.close=window.closeSheet=moving(window.closeSheet);
})()}catch(e){}
try{
(function(){
var notices=[],lastNotice='';
function renderNotices(){
var ul=G('qs-notices');
ul.textContent='';
if(!notices.length){
var empty=E('li');
empty.textContent='No recent notices';ul.appendChild(empty);
}
notices.forEach(function(item){
var li=E('li'),label=E('b');
label.textContent='Notice';li.appendChild(label);
var time=E('time');
time.textContent=item.clock;
time.setAttribute('datetime',item.at.toISOString());li.appendChild(time);
var text=E('span');
text.textContent=item.text;li.appendChild(text);
var dismiss=E('button');
dismiss.type='button';dismiss.textContent='Dismiss';
dismiss.setAttribute('aria-label','Dismiss notice');
dismiss.onclick=function(){try{
var index=notices.indexOf(item);
if(index>=0)notices.splice(index,1);
renderNotices();
}catch(e){}};
li.appendChild(dismiss);ul.appendChild(li);
});
var status=G('lock-notice'),text=notices.length?'New notice':'';
if(status&&status.textContent!==text)status.textContent=text;
}
function available(id,label,ready){
var b=G(id);
b.disabled=!ready;b.setAttribute('aria-disabled',ready?'false':'true');
b.textContent=label+(ready?'':' unavailable');
}
window.oposHooks=window.oposHooks||[];
window.oposHooks.push(function(s){try{
var qs=G('qs');
if(!qs){
qs=E('div');
qs.id='qs';
qs.hidden=true;
function button(id,label,action){
var b=E('button');
b.id=id;b.type='button';b.textContent=label;
b.onclick=function(){try{action();}catch(e){}};
qs.appendChild(b);
}
button('qs-close','Close',function(){if(typeof closeSheet==='function')closeSheet();});
button('qs-batt','',function(){
var details=G('time-left');
if(details&&typeof details.scrollIntoView==='function')details.scrollIntoView();
});
button('qs-helper','',function(){if(typeof toggleVis==='function'){
var pending=toggleVis();
if(pending&&typeof pending.catch==='function')pending.catch(function(){});
}});
button('qs-lock','Lock',function(){
if(typeof closeSheet==='function')closeSheet();
window.oposUI&&oposUI.lock&&oposUI.lock();
});
button('qs-settings','Settings',function(){
if(typeof closeSheet==='function')closeSheet();
window.oposUI&&oposUI.openApp&&oposUI.openApp('settings');
});
var list=E('ul');
list.id='qs-notices';list.setAttribute('aria-label','Recent notices');qs.appendChild(list);
var sheet=G('sheet');
if(!sheet)return;
var handle=G('sheet-handle');
sheet.insertBefore(qs,handle?handle.nextSibling:sheet.firstChild);
renderNotices();
}
available('qs-close','Close',typeof closeSheet==='function');
var details=G('time-left');
available('qs-batt','Battery: '+(s.battery&&typeof s.battery.percent==='number'&&isFinite(s.battery.percent)?Math.round(s.battery.percent)+'%':'unknown'),
!!details&&typeof details.scrollIntoView==='function');
available('qs-helper',s.helper_visible?'Helper: On':'Helper: Off',typeof toggleVis==='function');
G('qs-helper').setAttribute('aria-pressed',s.helper_visible?'true':'false');
G('qs-settings').hidden=!G('settings');
var notice=typeof s.notice==='string'?s.notice.trim():'';
if(notice&&notice!==lastNotice&&!String(s.status||'').startsWith('error:')){
var at=new Date();notices.unshift({text:s.notice,at:at,clock:demoTime?s.clock:('0'+at.getHours()).slice(-2)+':'+('0'+at.getMinutes()).slice(-2)});
notices=notices.slice(0,5);
renderNotices();
}
lastNotice=notice;
qs.hidden=false;
}catch(e){}});
})();
}catch(e){}
try{
(function(){
var time=G('sb-time');
var net=G('sb-net');
var engine=G('sb-engine');
var batt=G('sb-batt');
function tick(){try{
var d=new Date();
time.textContent=demoTime||('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
}catch(e){}}
window.oposHooks=window.oposHooks||[];
window.oposHooks.push(function(s){try{
var {clock}=s;if(demoTime&&clock)demoTime='Demo time '+clock;
tick();
net.textContent=navigator.onLine===false?'Offline':'';
batt.textContent=s.battery&&typeof s.battery.percent==='number'&&isFinite(s.battery.percent)?Math.round(s.battery.percent)+'%':'—';
engine.textContent=s.helper&&typeof s.helper.engine==='string'&&s.helper.engine?'Helper set to '+s.helper.engine:'';
}catch(e){}});
tick();
setInterval(tick,15000);
})();
}catch(e){}
try{
(function(){
var cover=document.getElementById('lock'),phone=document.getElementById('phone');
if(!cover||!phone)return;
var first=true,startY=null;
function lockTick(){try{
var d=new Date();
document.getElementById('lock-time').textContent=demoTime||('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
document.getElementById('lock-date').textContent=d.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'});
}catch(e){window.oposUI.unlock();}}
window.oposUI=window.oposUI||{};
function ownerOpen(name){
if(name==='sheet')window.oposUI.unlock();
else if(name==='lock'&&typeof window.closeSheet==='function')window.closeSheet();
}
if(typeof window.openSheet==='function'){
var openBase=window.openSheet;
window.openSheet=function(){
try{ownerOpen('sheet');}catch(e){window.oposUI.unlock();}
return openBase.apply(this,arguments);
};
if(window.opos)window.opos.open=window.openSheet;
}
// unlock definition start
window.oposUI.unlock=function(){
try{cover.hidden=true;}finally{phone.inert=false;}
};
// unlock definition end
window.oposUI.lock=function(){try{
ownerOpen('lock');
cover.hidden=false;
phone.inert=true;
document.getElementById('unlock').focus();
}catch(e){window.oposUI.unlock();}};
function safe(fn){return function(e){try{fn(e);}catch(err){window.oposUI.unlock();}};}
document.getElementById('unlock').onclick=safe(function(){window.oposUI.unlock();});
cover.addEventListener('pointerdown',safe(function(e){startY=e.clientY;}));
cover.addEventListener('pointerup',safe(function(e){
var up=startY!==null&&startY-e.clientY>40;
startY=null;
if(up)window.oposUI.unlock();
}));
cover.addEventListener('pointercancel',safe(function(){startY=null;}));
document.addEventListener('keydown',safe(function(e){if(e.key==='Escape')window.oposUI.unlock();}));
document.getElementById('lock-ask').onclick=safe(function(){
window.oposUI.unlock();
window.oposUI.openApp&&window.oposUI.openApp('helper');
});
document.getElementById('lock-call').onclick=safe(function(){
window.oposUI.unlock();
window.oposUI.openApp&&window.oposUI.openApp('phone');
});
document.getElementById('lock-hangup').onclick=safe(function(){
if(typeof go==='function')go('hang_up');
});
window.oposHooks=window.oposHooks||[];
window.oposHooks.push(function(s){try{
      lockTick();
document.getElementById('lock-batt').textContent=s.battery&&typeof s.battery.percent==='number'&&isFinite(s.battery.percent)?Math.round(s.battery.percent)+'%':'—';
var story=G('lock-story'),text=s.lock_story||'';if(story&&story.textContent!==text)story.textContent=text;
document.getElementById('lock-hangup').hidden=!s.in_call;
if(first){
        first=false;
if(!/(^|[?&])(lock=0|demo=1|app=full)(&|$)/.test(location.search))window.oposUI.lock();
}
}catch(e){window.oposUI.unlock();}finally{
phone.inert=!document.getElementById('lock').hidden;
}});
if(demoTime)lockTick();
setInterval(lockTick,15000);
})();
}catch(e){
var lockFallback=document.getElementById('lock'),phoneFallback=document.getElementById('phone');
if(lockFallback)lockFallback.hidden=true;
if(phoneFallback)phoneFallback.inert=false;
}
try{
(function(){
var reload=document.getElementById('reload-shell'),failures=0;
if(!reload)return;
function stateResult(failed){try{
failures=failed?failures+1:0;
reload.hidden=failures<2;
}catch(e){}}
reload.onclick=function(){try{location.reload();}catch(e){}};
window.oposHooks=window.oposHooks||[];
window.oposHooks.push(function(){stateResult(false);});
if(typeof window.api==='function'){
window.api=async function(o){
var r,s;
try{
r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':document.querySelector('meta[name=opos-token]').content},body:JSON.stringify(o)});
s=await r.json();
}catch(e){stateResult(true);throw e;}
stateResult(r.status>=500||r.status===403);
return s;
};
}
var screen=document.getElementById('screen');
if(screen&&typeof MutationObserver==='function'){
function bootFailed(){try{
if(screen.textContent==='Could not reach Old Phone OS Interface. Reload the page in a few seconds.'){
failures=2;reload.hidden=false;bootWatch.disconnect();
}
}catch(e){}}
var bootWatch=new MutationObserver(bootFailed);
bootWatch.observe(screen,{childList:true,characterData:true,subtree:true});
bootFailed();
window.oposHooks.push(function(){try{bootWatch.disconnect();}catch(e){}});
}
})();
}catch(e){}
// UXP20:
try{(function(){
function $(id){return document.getElementById(id)}
var saved=null,pending=false;
function remember(){try{
var a=document.activeElement,s=$('sheet'),l=$('lock');
saved={id:a&&a.id,
start:a&&a.selectionStart,end:a&&a.selectionEnd,direction:a&&a.selectionDirection,
top:s?s.scrollTop:0,
shade:window.opos&&opos.isOpen(),locked:l&&!l.hidden};
}catch(e){}}
function restore(r){try{
if(r!==saved)return;
var s=$('sheet'),l=$('lock');
if(!r||r.shade!==(window.opos&&opos.isOpen())||r.locked!==(l&&!l.hidden))return;
var a=r.id&&$(r.id),current=document.activeElement;
if(a&&current!==a&&current!==document.body&&current!==document.documentElement)return;
if(a){
if(current!==a)a.focus({preventScroll:true});
if(typeof r.start==='number')a.setSelectionRange(r.start,r.end,r.direction);
}
if(s&&s.scrollTop!==r.top)s.scrollTop=r.top;
}catch(e){}}
function resize(){try{
var old=saved;
if(document.activeElement!==document.body&&document.activeElement!==document.documentElement)remember();
if(old&&saved)saved.top=old.top;
var r=saved;pending=true;
requestAnimationFrame(function(){pending=false;restore(r)});
}catch(e){}}
['focusin','input','selectionchange','keyup','pointerup'].forEach(function(name){
document.addEventListener(name,remember,{passive:true});
});
var sheet=$('sheet');if(sheet)sheet.addEventListener('scroll',function(){if(!pending)remember()},{passive:true});
window.addEventListener('resize',resize,{passive:true});
if(window.visualViewport)window.visualViewport.addEventListener('resize',resize,{passive:true});
window.addEventListener('keydown',function(e){try{
if(!e.isTrusted||!e.code||e.key==='Unidentified'||e.isComposing||e.keyCode===229)return;
document.documentElement.setAttribute('data-kbd','1');
if(e.key!=='Enter'||e.repeat||e.defaultPrevented||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey)return;
if(e.target===$('msg')&&typeof go==='function'){
e.preventDefault();
var sent=go('send');if(sent&&sent.catch)sent.catch(function(){});
}
}catch(e){}});
remember();
})()}catch(e){}
// UXP23:
try{(function(){
function $(id){return document.getElementById(id)}
var helperDetails=null,resetBtn=$('helperreset'),restartBtn=$('helperrestart');
function known(v){return v!=null&&String(v).trim()&&!/not known/i.test(String(v))}
var jl=null;function jsc(){var sc=$('screen'),p=$('phone'),s=jl;if(sc&&p&&p.dataset.app==='home'){if(!s)return;var f=(s.screen&&s.screen[0])||'';sc.hidden=!(s.in_call||(f&&!/^Calls$/.test(f)))}else if(sc)sc.hidden=false}
try{new MutationObserver(function(){try{jsc()}catch(e){}}).observe($('phone'),{attributes:true,attributeFilter:['data-app']})}catch(e){}
window.oposHooks.push(function(s){try{
var home=$('home'),card=$('home-notice');
if(home&&!card){var fav=E('div');fav.id='home-favourites';card=E('div');card.id='home-notice';card.setAttribute('role','status');while(home.firstChild)fav.appendChild(home.firstChild);home.appendChild(fav);home.appendChild(card);home.classList.add('polished');$('phone').classList.add('polished')}
var notice=s.home_note||'';
if(card&&card.textContent!==notice)card.textContent=notice;
jl=s;jsc();
var percent=s.battery&&s.battery.percent;
$('sb-batt').setAttribute('data-low',typeof percent==='number'&&isFinite(percent)&&percent<=15?'true':'false');
var technical=$('shade-technical');['events'].forEach(function(id){technical.appendChild($(id))});
['history','history-label'].forEach(function(id){$(id).hidden=!(s.battery&&s.battery.history&&s.battery.history.length>1)});
['headline','time-left','goal-note','why'].forEach(function(id){var e=$(id);e.hidden=!known(e.textContent)});
['paused-list','features','events'].forEach(function(id){Array.prototype.forEach.call($(id).children,function(row){var label=row.querySelector('span');row.hidden=!known(label?label.textContent:row.textContent)})});
var helper=$('helper'),button=helper.querySelector('button');
if(button){
var lines=helper.textContent.split('\n').filter(function(line){var v=line.split(':').slice(1).join(':').trim();return v&&known(v)});
var details=E('details'),summary=E('summary');
details.open=!!(helperDetails&&helperDetails.open);
summary.textContent='Technical details';details.appendChild(summary);
lines.forEach(function(line){var row=E('div');row.textContent=line.replace(/Why\?$/,'');details.appendChild(row)});
helper.textContent='';helper.appendChild(details);if(resetBtn)helper.appendChild(resetBtn);if(restartBtn)helper.appendChild(restartBtn);
helperDetails=details;
var pb=$('planbtn'),ob=$('whybtn');if(ob)ob.parentNode.removeChild(ob);button.id='whybtn';if(pb)pb.parentNode.insertBefore(button,pb);else helper.appendChild(button);
}
}catch(e){}});
})()}catch(e){}
try{(function(){
var early=null;
window.oposHooks.push(function(s){early=s});
document.addEventListener('DOMContentLoaded',function(){try{
if(early)render(early);
else if(document.getElementById('screen').textContent)
fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}}).then(function(r){return r.json()}).then(function(s){if(s&&!s.error)return render(s)}).catch(function(){});
}catch(e){}},{once:true});
})()}catch(e){}
try{(function(){
var sb=G('sb'),lock=G('lock');
if(!sb||G('jaryo-shade'))return;
var row=E('div'),back=false;row.id='jaryo-status-row';
function add(id,text,fn){var b=E('button');b.type='button';b.id=id;b.textContent=text;
b.onclick=function(){try{fn()}catch(e){}};row.appendChild(b);return b}
var shade=add('jaryo-shade','Shade',function(){if(window.opos&&opos.open){back=true;opos.open()}});
add('jaryo-lock','Lock',function(){window.oposUI&&oposUI.lock&&oposUI.lock()});
sb.insertAdjacentElement('afterend',row);
if(lock){var sync=function(){row.inert=!lock.hidden};sync();
new MutationObserver(sync).observe(lock,{attributes:true,attributeFilter:['hidden']})}
var sheet=G('sheet'),generation=0;
if(sheet)new MutationObserver(function(){
var current=++generation;if(sheet.getAttribute('aria-hidden')!=='true')return;
var target=back?shade:G('meter');back=false;
function ownsFocus(){var active=document.activeElement;
return active===document.body||active===target||active===G('meter')||sheet.contains(active)}
if(!ownsFocus())return;
requestAnimationFrame(function(){try{
if(current===generation&&sheet.getAttribute('aria-hidden')==='true'&&ownsFocus()&&target&&!target.closest('[inert]'))target.focus();
}catch(e){}});
}).observe(sheet,{attributes:true,attributeFilter:['aria-hidden']});
var base=window.closeSheet;
if(typeof base==='function'){var close=function(){var r=base.apply(this,arguments);
try{if(back&&!row.inert)shade.focus()}catch(e){}return r};
window.closeSheet=close;if(window.opos)window.opos.close=close}
})()}catch(e){}
