// phone-b.js: phone-shell hook point (UXP00)
var G=i=>document.getElementById(i),E=t=>document.createElement(t);
try{(function(){
var FF=['slab','foldable','slideout','tablet'],d=document.documentElement,v='auto';
var $=G;
var TWO='(horizontal-viewport-segments:2)';
function apply(x){if(x==='slide')x='slideout';if(FF.indexOf(x)<0)x='auto';v=x;if(x!=='auto')d.dataset.ff=x;else if(mq(TWO))d.dataset.ff='foldable';else delete d.dataset.ff}
function mq(q){try{return window.matchMedia(q).matches}catch(e){return false}}
function guess(){try{apply(v)}catch(e){}var g=$('ff-detected');if(g)g.textContent='Auto looks like: '+(mq(TWO)?'two screens':mq('(min-width:600px) and (min-aspect-ratio:3/4) and (max-aspect-ratio:4/3)')?'foldable':mq('(orientation:landscape) and (max-height:520px)')?'slide-out':'slab')}
try{v=localStorage.getItem('opos_ff')}catch(e){v='auto'}
try{apply(v)}catch(e){}
try{var f=$('ff'),r=f&&f.querySelector('input[value="'+v+'"]');if(r)r.checked=true;guess();window.addEventListener('resize',guess);
if(f)f.addEventListener('change',function(e){var s=$('ff-saved');try{apply(e.target.value)}catch(_){}
try{if(v==='auto')localStorage.removeItem('opos_ff');else localStorage.setItem('opos_ff',v);if(s)s.textContent='Saved'}catch(_){if(s)s.textContent='Could not save'}})
}catch(e){}
})()}catch(e){}
try{(function(){
var ui=window.oposUI=window.oposUI||{},APPS=['home','phone','messages','helper','settings'],first=true,last='',pend=false;
var $=G;
function full(){var p=$('phone'),h=$('home'),b=$('back'),s=$('settings');if(p)delete p.dataset.app;if(h)h.hidden=true;if(b)b.hidden=true;if(s)s.hidden=true}
function done(){pend=false;try{var p=$('phone'),a=$('ask');if(!p||!a||p.dataset.app!=='helper')return;if(a.hidden){full();return}
if(typeof a.scrollIntoView==='function')a.scrollIntoView({block:'nearest'});var q=$('q');if(q)q.focus()}catch(e){full()}}
ui.openApp=function(name){
if(APPS.indexOf(name)<0)name='home';
if(name==='settings'&&!$('settings'))name='home';
var p=$('phone'),h=$('home'),b=$('back'),s=$('settings');if(!p)return;
try{
var prev=p.dataset.app;
p.dataset.app=name;
if(typeof scopeNotice==='function')scopeNotice();
if(h)h.hidden=name!=='home';if(b)b.hidden=name==='home';if(s)s.hidden=name!=='settings';
try{window.scrollTo(0,0)}catch(e){}
if(name==='home'){var t=last&&h&&h.querySelector('[data-open='+last+']');if(t&&prev&&prev!=='home')t.focus();return}
last=name;
if(name!=='helper'){pend=false;if(b)b.focus();return}
var a=$('ask');if(!a)return;
if(pend)return;
if(a.hidden&&typeof toggleVis==='function'&&!(typeof vis!=='undefined'&&vis)){pend=true;Promise.resolve().then(toggleVis).then(function(){setTimeout(function(){if(pend)done()},0)},function(){if(pend)done()}).catch(full)}else done();
}catch(e){full()}
};
var h=$('home');if(h)h.addEventListener('click',function(e){var t=e.target.closest('[data-open]');if(t)ui.openApp(t.dataset.open)});
var b=$('back');if(b)b.addEventListener('click',function(){ui.openApp('home')});
window.addEventListener('keydown',function(e){if(e.key!=='Escape')return;var l=$('lock'),p=$('phone');if(l&&!l.hidden)return;if(window.opos&&opos.isOpen&&opos.isOpen())return;if(p&&p.dataset.app)ui.openApp('home')},true);
(window.oposHooks=window.oposHooks||[]).push(function(){
var s=h&&h.querySelector('[data-open=settings]');if(s)s.hidden=!$('settings');
var a=$('ask'),w=$('helper-where');if(a&&w)w.hidden=a.hidden;if(pend&&a&&!a.hidden)done();
if(!first)return;first=false;if(!/(^|[?&])(demo=1|app=full)(&|$)/.test(location.search))ui.openApp('home')});
})()}catch(e){}
try{(function(){
var a=G('ask'),c=G('webq');if(!a||!a.parentNode||G('helper-where'))return;
var w=E('p');w.id='helper-where';w.hidden=a.hidden;a.insertBefore(w,G('q').nextSibling);
var lan=false,simulated=false;
function set(){w.textContent=(simulated?'Demo phone. ':'')+(c&&c.checked?'Web search is on: your question is sent to Wikipedia.':lan?'Answers may be made on a computer you own. Web search is off.':'Answers are made on this phone when possible. Web search is off.')}
set();if(c)c.addEventListener('change',set);
(window.oposHooks=window.oposHooks||[]).push(function(s){var e=s&&s.helper&&s.helper.engine;if(typeof e!=='string')return;var x=/computer you own/.test(e),y=!!(s.simulation&&s.simulation.modem===true);if(x!==lan||y!==simulated){lan=x;simulated=y;set()}});
try{var n=G('answer');if(n&&typeof MutationObserver==='function')new MutationObserver(function(){try{set()}catch(e){}}).observe(n,{childList:true,characterData:true})}catch(e){}
})()}catch(e){}
try{(function(){
var ui=window.oposUI;var $=G;
var p=$('phone'),h=$('home'),a=$('allapps'),ul=$('apps-list'),x=$('apps-close');if(!ui||!ui.openApp||!p||!h||!a||!ul||!x)return;
var L=[['phone','Phone'],['messages','Messages'],['helper','Helper'],['settings','Settings'],['contacts','Contacts','phone'],['threads','Threads','messages']],open=false,o=ui.openApp;
L.forEach(function(e){var li=E('li'),b=E('button');b.type='button';b.textContent=e[1];b.dataset.open=e[2]||e[0];if(e[2])b.dataset.scroll=e[0];li.appendChild(b);ul.appendChild(li)});
function hide(){open=false;delete p.dataset.allapps;ul.hidden=true;x.hidden=true}
function show(){if(p.dataset.app!=='home')return;var f=null,i,li,b,t,k=$('back');
try{for(i=0;i<ul.children.length;i++){li=ul.children[i];b=li.firstChild;if(!b||!b.dataset)continue;if(!b.dataset.open)continue;t=b.dataset.scroll?$(b.dataset.scroll):h.querySelector('[data-open='+b.dataset.open+']');li.hidden=!t||!!t.hidden;if(!li.hidden&&!f)f=b}
open=true;p.dataset.allapps='1';ul.hidden=false;x.hidden=false;if(k)k.hidden=false;h.hidden=true}
catch(e){try{hide()}catch(e2){}h.hidden=false;if(k)k.hidden=true;return}
try{if(f)f.focus()}catch(e){}}
a.addEventListener('click',show);
x.addEventListener('click',function(){ui.openApp('home')});
ul.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('button');if(!b)return;ui.openApp(b.dataset.open);
var t=b.dataset.scroll&&$(b.dataset.scroll);if(t&&p.dataset.app===b.dataset.open&&typeof t.scrollIntoView==='function')t.scrollIntoView({block:'start'})});
ui.openApp=function(n){var w=open;try{hide()}catch(e){}var r=o.apply(this,arguments);try{if(w&&p.dataset.app==='home')a.focus()}catch(e){}return r};
})()}catch(e){}
try{(function(){
var d=document.documentElement,T=['s','n','l','xl'],s=G('textsize'),v=null;
function set(x){if(T.indexOf(x)<0)x='n';d.dataset.text=x;if(s)s.value=x;return x}
try{v=localStorage.getItem('opos_text')}catch(e){}
set(v);
if(s)s.addEventListener('change',function(){var x=set(s.value);try{localStorage.setItem('opos_text',x)}catch(e){}});
})()}catch(e){}
try{(function(){
var n=G('demo-phone-note');if(!n)return;
function set(s){n.textContent=s&&s.simulation&&s.simulation.modem===true?'Demo: calls and texts are simulated.':'Calls and texts may use your configured phone connection.'}
set();(window.oposHooks=window.oposHooks||[]).push(set);
})()}catch(e){}
// UXP31:
function helperAsk(text,web,request){return request({action:'ask',text:text,web:web}).then(function(r){if(r&&r.error)r.error=r.error==='helper hidden'?'Tap Show helper first':r.error;return r})}
try{(function(){
var ui=window.oposUI;if(!ui||!ui.openApp)return;
var o=ui.openApp,built=false,dead=false,T=[],MAX=6,ASK_WAIT_MS=125000,ctl=null,timer=null,pulse=null,lastQ='',ask,log,st,bar,bs,bc,br,resting=false,modeVersion=0,REST='The helper is resting to save power. Plug the phone in, then ask again.',STD='The helper did not reply.';
var $=G;
function mk(t){return document.createElement(t)}
function btn(id,t){var b=mk('button');b.type='button';b.id=id;b.textContent=t;return b}
function clearWait(){if(timer!==null){clearTimeout(timer);timer=null}if(pulse!==null){clearInterval(pulse);pulse=null}}
function off(){dead=true;clearWait();try{if(ctl)ctl.abort()}catch(e){}ctl=null;try{bar.hidden=true;log.hidden=true;st.textContent=''}catch(e){}if(ask)delete ask.dataset.hs}
function g(f){return function(){if(dead)return;try{return f.apply(this,arguments)}catch(e){off()}}}
function row(t){if(!t.li){t.li=mk('li');var q=mk('p');t.ap=mk('p');q.textContent='You: '+t.q;t.li.appendChild(q);t.li.appendChild(t.ap)}
if(t.shown===t.a)return t.li;t.shown=t.a;t.ap.textContent='Helper: '+(t.a||'...');(t.src||[]).forEach(function(s){if(!s||typeof s.title!=='string')return;var a=mk('a');a.textContent=s.title;
if(typeof s.url==='string'&&s.url.indexOf('https://en.wikipedia.org/')===0)a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';t.ap.appendChild(mk('br'));t.ap.appendChild(a)});return t.li}
function draw(){var k=T.map(row);while(log.firstChild&&k.indexOf(log.firstChild)<0)log.removeChild(log.firstChild);k.forEach(function(li){if(li.parentNode!==log)log.appendChild(li)});log.hidden=!T.length}
function std(j){return j&&typeof j.text==='string'&&j.text.trim()||STD+' You can try again.'}
function setRest(v){resting=v;if(!bs)return;bs.disabled=resting||!!ctl;if(!ctl){if(resting){st.textContent=REST;br.hidden=true}else if(st.textContent===REST)st.textContent=''}}
(window.oposHooks=window.oposHooks||[]).push(function(s){var m=s&&s.helper&&s.helper.mode;if(m==='CRITICAL'||m==='NORMAL'||m==='SAVER'){modeVersion++;setRest(m==='CRITICAL'&&!(s.helper.usable_percent>=15))}});
function end(t,a,s,retry,src){try{clearWait();ctl=null;t.a=a||'(no answer)';t.src=src;draw();bs.disabled=resting;bc.disabled=true;st.textContent=resting?REST:s;br.hidden=resting||!retry}catch(e){off()}}
function send(q){q=String(q||'').trim();if(!q||ctl||resting)return;lastQ=q;br.hidden=true;
var t={q:q,a:''},c=new AbortController(),started=Date.now(),askedModeVersion=modeVersion,m=document.querySelector('meta[name=opos-token]'),w=$('webq');c.turn=t;
T.push(t);while(T.length>MAX)T.shift();ctl=c;draw();bs.disabled=true;bc.disabled=false;
function waiting(){if(ctl===c)st.textContent='Waiting... '+Math.max(0,Math.floor((Date.now()-started)/1000))+' s elapsed. Cancel to stop waiting.'}
waiting();pulse=setInterval(waiting,5000);
timer=setTimeout(function(){if(ctl!==c||dead)return;c.abort();end(t,std(null),'No reply after '+(ASK_WAIT_MS/1000)+' s. Tap Retry.',true)},ASK_WAIT_MS);
helperAsk(q,!!(w&&w.checked),function(payload){return fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':m?m.content:''},body:JSON.stringify(payload),signal:c.signal})
.then(function(r){return r.json().then(function(j){if(r.ok)return j;return{error:r.status===429?'Too many questions in a row. Wait a few seconds, then try again.':r.status===403?'This page is out of date. Reload it, then try again.':r.status>=500?'Something went wrong. Try again in a minute.':j&&j.error||'The request failed. Try again.'}},function(){return{ok:false}})})})
.then(function(j){if(ctl!==c||dead)return;if(j&&j.error)return end(t,j.error,j.error,true);if(j&&j.ok===false&&j.engine==='rules'&&typeof j.text==='string'&&j.text.trim()){if(modeVersion===askedModeVersion)setRest(true);return end(t,j.text.trim(),resting?REST:'This request rested to save power. You can ask again.',false)}if(!j||j.ok===false)return end(t,std(j),'No reply. Tap Retry.',true);
var text=typeof j.text==='string'?j.text.trim():'';if(!text)return end(t,std(j),'No reply. Tap Retry.',true);
var input=$('q');if(input&&input.value.trim()===q)input.value='';
end(t,text+(j.engine&&j.engine!=='none'&&j.where?' (reply: '+j.engine+', '+j.where+')':''),'Answered.',false,Array.isArray(j.sources)?j.sources:null)})
.catch(function(){if(ctl!==c||dead)return;end(t,std(null),'No reply. Tap Retry.',true)})
.catch(off)}
function build(){var a=$('ask'),q=$('q');if(!a||!q||q.parentNode!==a||typeof AbortController!=='function'||typeof fetch!=='function')return;
var l=mk('ol'),s=mk('p'),b=mk('div');ask=a;bar=b;
l.id='hs-log';l.setAttribute('role','log');l.setAttribute('aria-live','polite');l.setAttribute('aria-label','Helper conversation');s.id='hs-status';s.setAttribute('role','status');s.setAttribute('aria-live','polite');b.id='hs-bar';
var b1=btn('hs-send','Send'),b2=btn('hs-cancel','Cancel'),b3=btn('hs-retry','Retry'),b4=btn('hs-clear','Clear');b2.disabled=true;b3.hidden=true;
[b1,b2,b3,b4].forEach(function(x){b.appendChild(x)});
b1.addEventListener('click',g(function(){send(q.value)}));
b2.addEventListener('click',g(function(){if(ctl){var c=ctl;c.abort();end(c.turn,'Cancelled.','Cancelled.',true)}}));
b3.addEventListener('click',g(function(){send(lastQ)}));
b4.addEventListener('click',g(function(){T=[];draw();if(!ctl)st.textContent=resting?REST:''}));
q.addEventListener('keydown',g(function(e){if(e.key==='Enter'&&!e.isComposing&&!e.repeat&&!e.defaultPrevented){e.preventDefault();send(q.value)}}));
log=l;st=s;bs=b1;bc=b2;br=b3;draw();
a.insertBefore(l,q);a.insertBefore(s,q.nextSibling);a.insertBefore(b,s);a.dataset.hs='1';built=true;setRest(resting)}
ui.openApp=function(n){var r=o.apply(this,arguments);try{if(n==='helper'&&!built&&!dead)build()}catch(e){off()}return r};
})()}catch(e){}
