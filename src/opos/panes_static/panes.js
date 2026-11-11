// SPDX-License-Identifier: GPL-3.0-or-later
// panes.js: the Hand setting and the two-pane switch. Served at /panes.js by opos.panes while OPOS_PANES=1; loaded in the head, so
// html[data-hand] is set before the first paint. Fail-open: if anything here throws, the page is what jaryo.css paints. Storage is read for
// the first value and when another tab writes it, and written on a tap; the label and the next tap go by the page's own data-hand, so a
// blocked or full store cannot make the label disagree with the layout.
try{(function(){
var d=document.documentElement,KEY='jaryo_hand',P=location.pathname,MODES='@MODES@'.split(',');
function stored(){try{return localStorage.getItem(KEY)}catch(e){return null}}
function put(v){try{localStorage.setItem(KEY,v)}catch(e){}}
function hand(){return stored()==='left'?'left':'right'}
function cur(){return d.dataset.hand==='left'?'left':'right'}
function label(){var b=document.getElementById('jaryo-hand');if(b)b.textContent='Hand: '+(cur()==='left'?'Left':'Right')}
function setHand(v){d.dataset.hand=v;label()}
function kind(){if(P==='/')return 'app';if(P==='/network'||P==='/firstrun'||P==='/timezone')return 'page';try{if(d.getAttribute&&d.getAttribute('data-opos-app')!=null)return 'page'}catch(e){}return ''}
function sync(){var k=kind();if(k==='page'||(k==='app'&&d.dataset.ff==='foldable'))d.dataset.panes='two';else delete d.dataset.panes;try{detail()}catch(e){}}
function mount(){try{var t=document.getElementById('jaryo-theme');if(!t||document.getElementById('jaryo-hand'))return;
var b=document.createElement('button');b.type='button';b.id='jaryo-hand';
b.onclick=function(){var v=cur()==='left'?'right':'left';put(v);setHand(v)};
t.insertAdjacentElement('afterend',b);label()}catch(e){}}
setHand(hand());sync();
try{new MutationObserver(sync).observe(d,{attributes:true,attributeFilter:['data-ff']})}catch(e){}
try{window.addEventListener('storage',function(e){if(e.key===KEY||e.key===null)setHand(hand())})}catch(e){}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
function detail(){try{
var box=document.getElementById('pane-detail');
if(d.dataset.panes!=='two'){if(box&&box.remove)box.remove();return}
var phone=document.getElementById('phone');
if(!phone)return;
if(!box){box=document.createElement('div');box.id='pane-detail';box.setAttribute('role','status');phone.insertBefore(box,phone.firstChild)}
var lines={phone:'Practice calls stay on this phone.',messages:'Practice messages stay on this phone.',helper:'Ask a question. The answer stays on this phone.',settings:'Display, sound, and the lock.'};
function show(btn){var pane=document.getElementById('pane-detail');if(!pane||!btn)return;var name=btn.getAttribute('data-open')||'',label=(btn.textContent||'').replace(/\s+/g,' ').trim(),extra=lines[name]||'';pane.textContent=label?(label+(extra?'. '+extra:'')):'';}
var home=document.getElementById('home');
if(!home)return;
if(!home.dataset.detail){home.dataset.detail='1';
function pick(e){var b=e.target&&e.target.closest&&e.target.closest('button');if(b&&home.contains(b))show(b)}
home.addEventListener('focusin',pick);home.addEventListener('pointerover',pick);}
show(home.querySelector('button'));
}catch(e){}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',detail);else detail();
})()}catch(e){}
