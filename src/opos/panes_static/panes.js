// SPDX-License-Identifier: GPL-3.0-or-later
// panes.js: the Hand setting and the two-pane switch. Served at /panes.js by opos.panes while OPOS_PANES=1; loaded in the head, so
// html[data-hand] is set before the first paint. Fail-open: if anything here throws, the page is what jaryo.css paints.
try{(function(){
var d=document.documentElement,KEY='jaryo_hand',P=location.pathname,MODES='@MODES@'.split(',');
function stored(){try{return localStorage.getItem(KEY)}catch(e){return null}}
function put(v){try{localStorage.setItem(KEY,v)}catch(e){}}
function hand(){return stored()==='left'?'left':'right'}
function label(){var b=document.getElementById('jaryo-hand');if(b)b.textContent='Hand: '+(hand()==='left'?'Left':'Right')}
function setHand(v){d.dataset.hand=v;label()}
function kind(){return P==='/'?'app':P==='/network'||P==='/firstrun'||P==='/timezone'?'page':''}
function sync(){var k=kind();if(k==='page'||(k==='app'&&d.dataset.ff==='foldable'))d.dataset.panes='two';else delete d.dataset.panes}
function mount(){try{var t=document.getElementById('jaryo-theme');if(!t||document.getElementById('jaryo-hand'))return;
var b=document.createElement('button');b.type='button';b.id='jaryo-hand';
b.onclick=function(){var v=hand()==='left'?'right':'left';put(v);setHand(v)};
t.insertAdjacentElement('afterend',b);label()}catch(e){}}
setHand(hand());sync();
try{new MutationObserver(sync).observe(d,{attributes:true,attributeFilter:['data-ff']})}catch(e){}
try{window.addEventListener('storage',function(e){if(e.key===KEY||e.key===null)setHand(hand())})}catch(e){}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})()}catch(e){}
