// jaryo.js: Jaryo theme and motion
var G=i=>document.getElementById(i),E=t=>document.createElement(t);
try{(function(){
var jaryo=window.jaryo||{};window.jaryo=jaryo;
var IN='cubic-bezier(.2,.8,.2,1)',OUT='cubic-bezier(.4,0,1,1)',active=[],els=[],generation=0;
var ROOT={messages:'threads',helper:'helper',settings:'settings'};
function $(i){return document.getElementById(i)}
function root(n){return $(ROOT[n])||$('screen')}
function tileFor(n){var h=$('home');return n&&h&&h.querySelector?h.querySelector('[data-open='+n+']'):null}
function reduced(){try{if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)return true}catch(e){}try{return window.localStorage.getItem('jaryo_motion')==='off'}catch(e){return false}}
function save(el,names){var r={el:el,values:{},done:false};names.forEach(function(n){r.values[n]=[el.style.getPropertyValue(n),el.style.getPropertyPriority(n)]});els.push(r);return r}
function cleanup(r){if(r.done)return;r.done=true;try{if(r.obs)r.obs.disconnect();Object.keys(r.values).forEach(function(n){var v=r.values[n];if(v[0])r.el.style.setProperty(n,v[0],v[1]);else r.el.style.removeProperty(n)});if(r.inert!==undefined)r.el.inert=r.inert}catch(e){}}
function cancelActive(){generation++;var a=active,e=els;active=[];els=[];for(var i=0;i<a.length;i++){try{a[i].cancel()}catch(x){}}for(i=0;i<e.length;i++)cleanup(e[i])}
function run(el,k,d,ease,r){r=r||save(el,['will-change','transform-origin']);el.style.setProperty('will-change',k[0].transform?'transform,opacity':'opacity');if(k[0].transform)el.style.setProperty('transform-origin','0 0');var a=el.animate(k,{duration:d,easing:ease,fill:'none'});active.push(a);a.onfinish=a.oncancel=function(){cleanup(r)};return a}
function play(list){var g=generation,go=function(){if(g!==generation)return;try{for(var i=0;i<list.length;i++)if(list[i][0])run.apply(null,list[i])}catch(e){cancelActive()}};if(window.requestAnimationFrame)window.requestAnimationFrame(go);else go()}
function fade(a,b){return[[a,[{opacity:1},{opacity:0}],120,OUT],[b,[{opacity:0},{opacity:1}],120,IN]]}
function crossfade(a,b){cancelActive();play(fade(a,b))}
function rect(el){try{return el.getBoundingClientRect()}catch(e){return null}}
function flip(f,t){if(!f||!t||!t.width||!t.height)return'none';return'translate('+(f.left-t.left)+'px,'+(f.top-t.top)+'px) scale('+(f.width/t.width)+','+(f.height/t.height)+')'}
function move(out,inn,fr,tr,d){var cur=null;try{if(active.length)cur=window.getComputedStyle(inn).transform}catch(e){}cancelActive();if(reduced())return play(fade(out,inn));var st=cur&&cur!=='none'?cur:flip(fr,tr);play([[out,[{opacity:1},{opacity:0}],d,OUT],[inn,[{transform:st,opacity:0},{transform:'none',opacity:1}],d,IN]])}
function zoomIn(a,b){try{if(a&&b)move(a,b,rect(a),rect(b),240)}catch(e){cancelActive()}}
function back(out,inn,fr,tr,display,cur){cancelActive();if(!fr||!tr||!fr.width||!fr.height)return;var r=save(out,['will-change','transform-origin','display','position','left','top','width','height','min-width','min-height','max-width','max-height','margin','box-sizing']);r.inert=out.inert;out.inert=true;try{if(window.MutationObserver){r.obs=new MutationObserver(function(){if(!r.done&&!out.hidden)cancelActive()});r.obs.observe(out,{attributes:true,attributeFilter:['hidden']})}}catch(e){}var st={display:display&&display!=='none'?display:'block',position:'fixed',left:fr.left+'px',top:fr.top+'px',width:fr.width+'px',height:fr.height+'px','min-width':'0','min-height':'0','max-width':'none','max-height':'none',margin:'0','box-sizing':'border-box'};if(window.getComputedStyle(out).display==='none'||out.hidden)Object.keys(st).forEach(function(n){out.style.setProperty(n,st[n],'important')});var list=reduced()?fade(out,inn):[[out,[{transform:cur&&cur!=='none'?cur:'none',opacity:1},{transform:flip(tr,fr),opacity:0}],240,OUT],[inn,[{opacity:0},{opacity:1}],240,IN]];list[0].push(r);play(list)}
function zoomOut(a,b){try{if(a&&b)back(a,b,rect(a),rect(b),window.getComputedStyle(a).display)}catch(e){cancelActive()}}
function through(a,b,dir){try{if(!a||!b)return;cancelActive();if(reduced())return play(fade(a,b));var x=dir==='left'?-8:dir==='right'?8:0;play([[a,[{transform:'scale(1)',opacity:1},{transform:'scale(.94) translateX('+x+'%)',opacity:0}],180,OUT],[b,[{transform:'scale(.94) translateX('+(-x)+'%)',opacity:0},{transform:'scale(1)',opacity:1}],180,IN]])}catch(e){cancelActive()}}
function lock(el){try{if(!el)return;cancelActive();play(reduced()?[[el,[{opacity:1},{opacity:0}],120,OUT]]:[[el,[{transform:'scale(1)',opacity:1},{transform:'scale(1.04)',opacity:0}],200,OUT]])}catch(e){cancelActive()}}
function zoomOpen(tileId,app,o){o=o||orig;var p=$('phone'),prev=p&&p.dataset.app,t=null,fr=null;try{cancelActive();t=(tileId&&$(tileId))||tileFor(app);fr=rect(t)}catch(e){}var r=o?o(app):undefined;try{if(!p||p.dataset.app===prev||p.dataset.app==='home')return r;jaryo._lastTile=p.dataset.app;var el=root(p.dataset.app);if(t&&el)move(t,el,fr,rect(el),240)}catch(e){cancelActive()}return r}
function zoomBack(o){o=o||orig;var p=$('phone'),prev=p&&p.dataset.app,from=null,fr=null,display=null,cur=null;try{from=prev&&prev!=='home'?root(prev):null;if(from){var cs=window.getComputedStyle(from);display=cs.display;cur=cs.transform}cancelActive();fr=rect(from)}catch(e){}var r=o?o('home'):undefined;try{var t=tileFor(jaryo._lastTile)||tileFor(prev);if(t){if(from)back(from,t,fr,rect(t),display,cur);if(prev&&prev!=='home'&&document.activeElement!==t)t.focus()}}catch(e){cancelActive()}return r}
var orig=null;
jaryo.motion=function(){return reduced()?'reduced':'full'};
jaryo.motion.zoomIn=zoomIn;jaryo.motion.zoomOut=zoomOut;jaryo.motion.through=through;jaryo.motion.lock=lock;jaryo.motion.reduced=reduced;jaryo.motion.crossfade=crossfade;
jaryo.zoomOpen=zoomOpen;jaryo.zoomBack=zoomBack;
try{var ui=window.oposUI;if(ui&&ui.openApp){orig=ui.openApp;ui.openApp=function(n){return n==='home'?zoomBack(orig):zoomOpen(null,n,orig)}}}catch(e){}
try{
var initType=function(){
try{
var phone=G('phone');
if(!phone)return;
if(!G('jaryo-masthead')){
var div=E('div');
div.id='jaryo-masthead';
var s1=E('span');
s1.textContent='Old Phone OS Interface';
var br=E('br');
var s2=E('span');
var d=new Date();
var ds=d.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'});
s2.textContent=ds;
div.appendChild(s1);
div.appendChild(br);
div.appendChild(s2);
if(phone.firstChild){
phone.insertBefore(div,phone.firstChild);
}else{
phone.appendChild(div);
}
}
var heads=phone.querySelectorAll('h1,h2');
for(var i=0;i<heads.length;i++){
var h=heads[i];
if(!h.classList.contains('jaryo-head')){
h.classList.add('jaryo-head');
}
}
}catch(e){}
};
if(document.readyState==='loading'){
document.addEventListener('DOMContentLoaded',initType);
}else{
initType();
}
}catch(e){}
})()}catch(e){}
try{(function(){
var done={},d=document.documentElement,b=G('jaryo-theme'),theme=null,themeSet=false;
function stored(k){try{return localStorage.getItem(k)}catch(e){return null}}
function paint(v){theme=v==='light'||v==='dark'?v:null;
try{if(theme)d.dataset.theme=theme;else delete d.dataset.theme}catch(e){}
if(b)b.textContent='Theme: '+(theme==='light'?'Light':theme==='dark'?'Dark':'System')}
function saveTheme(manual){try{if(theme)localStorage.setItem('jaryo_theme',theme);else localStorage.removeItem('jaryo_theme');
if(manual)localStorage.setItem('jaryo_theme_set','1')}catch(e){}}
var saved=stored('jaryo_theme');themeSet=saved==='light'||saved==='dark'||stored('jaryo_theme_set')==='1';paint(saved);
if(b)b.onclick=function(){themeSet=true;paint(theme==='light'?'dark':theme==='dark'?null:'light');saveTheme(true)};
function seed(key,value,apply){if(done[key])return;var old=stored(key);
if(old===null){old=value;try{if(value!==null)localStorage.setItem(key,value)}catch(e){}}
try{apply(old);done[key]=true}catch(e){}}
(window.oposHooks=window.oposHooks||[]).push(function(s){try{
var p=s&&s.prefs;if(!p)return;
if(!themeSet){paint(p.theme);saveTheme(false);themeSet=true}
seed('opos_text',({small:'s',large:'l',xlarge:'xl'})[p.text_size]||'n',function(v){
if(['s','n','l','xl'].indexOf(v)<0)v='n';d.dataset.text=v;
var t=G('textsize');if(t)t.value=v});
seed('opos_ff',p.device_type||'auto',function(v){
if(['slab','foldable','slideout','tablet'].indexOf(v)<0){v='auto';delete d.dataset.ff}else d.dataset.ff=v;
var f=G('ff'),r=f&&f.querySelector('input[value="'+v+'"]');if(r)r.checked=true});
}catch(e){}});
document.addEventListener('change',function(e){try{
var t=e.target;if(t.id==='ff'||(t.closest&&t.closest('#ff')))localStorage.setItem('opos_ff',t.value);
}catch(e){}});
})()}catch(e){}
