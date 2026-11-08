(function(){
'use strict';
var body=document.getElementById('mon-body');
var status=document.getElementById('mon-status');
var live=document.getElementById('mon-live');
var meta=document.querySelector('meta[name=opos-token]');
var token=meta?meta.content:'';
var drawn=false,busy=false,timer=null,okAt=0,lastFailure=null,shape='',boxes=[];

function now(){return typeof performance==='object'&&performance&&performance.now?performance.now():Date.now()}
function el(tag,cls,text){
  var n=document.createElement(tag);
  if(cls)n.className=cls;
  if(typeof text==='string')n.textContent=text;
  return n;
}
function str(v,max){
  if(typeof v!=='string')return '';
  return v.length>max?v.slice(0,max-1)+'…':v;
}
function list(v,max){return Array.isArray(v)?v.slice(0,max):[]}
function put(node,text){if(node&&node.textContent!==text)node.textContent=text}
function speak(text){if(live&&live.textContent!==text)live.textContent=text}

// One section of the reply, cut to what this page shows. Nothing here is markup.
function parts(s){
  return {
    id:str(s.id,20),title:str(s.title,60),glyph:str(s.glyph,4),word:str(s.status,40),
    lines:list(s.lines,12).map(function(l){return str(l,600)}),
    facts:list(s.facts,40).filter(Array.isArray).map(function(f){return [str(f[0],40),str(f[1],120)]}),
    pre:typeof s.pre==='string'&&s.pre?s.pre.slice(0,2000):''
  };
}
function shapeOf(p){return p.id+'|'+p.lines.length+'|'+p.facts.length+'|'+(p.pre?1:0)}

function build(p){
  var box=el('section','mon-section'),r={box:box,lines:[],dt:[],dd:[],pre:null};
  r.title=el('h2',null,p.title);
  box.appendChild(r.title);
  var head=el('p','mon-head');
  r.glyph=el('span','mon-glyph',p.glyph);
  r.word=el('span','mon-word',p.word);
  head.appendChild(r.glyph);
  head.appendChild(r.word);
  box.appendChild(head);
  p.lines.forEach(function(t){var n=el('p','mon-line',t);r.lines.push(n);box.appendChild(n)});
  if(p.facts.length){
    var dl=el('dl','mon-facts');
    p.facts.forEach(function(f){
      var a=el('dt',null,f[0]),b=el('dd',null,f[1]);
      r.dt.push(a);r.dd.push(b);dl.appendChild(a);dl.appendChild(b);
    });
    box.appendChild(dl);
  }
  if(p.pre){r.pre=el('pre','mon-pre',p.pre);box.appendChild(r.pre)}
  return r;
}
// Same shape as last time: change only the text that changed, so a selection or a scrolled peer map stays put.
function fill(r,p){
  put(r.title,p.title);put(r.glyph,p.glyph);put(r.word,p.word);
  p.lines.forEach(function(t,i){put(r.lines[i],t)});
  p.facts.forEach(function(f,i){put(r.dt[i],f[0]);put(r.dd[i],f[1])});
  if(r.pre)put(r.pre,p.pre);
}

function render(doc){
  var ps=list(doc&&doc.sections,4).filter(function(s){return s&&typeof s==='object'}).map(parts);
  if(!ps.length)throw new Error('bad');
  var key=ps.map(shapeOf).join('#');
  if(key===shape&&boxes.length===ps.length){
    ps.forEach(function(p,i){fill(boxes[i],p)});
  }else{
    while(body.firstChild)body.removeChild(body.firstChild);
    boxes=ps.map(build);
    boxes.forEach(function(r){body.appendChild(r.box)});
    shape=key;
  }
  okAt=now();
  body.className='';
  var ago=doc.updatedAgoSeconds;
  status.textContent=(typeof ago==='number'&&isFinite(ago))?'Updated '+Math.max(0,Math.round(ago))+' s ago.':'Checking.';
  if(lastFailure!==null){lastFailure=null;speak('Connected again.')}
  drawn=true;
}

// The age lives in the visible status line only; the live region is told about a change of state, once.
function failed(message){
  var text=message;
  if(drawn){
    text+=' Last good answer '+Math.max(0,Math.round((now()-okAt)/1000))+' s ago.';
    body.className='mon-stale';
  }
  status.textContent=text;
  if(lastFailure!==message){lastFailure=message;speak(message)}
}

function stop(){if(timer!==null){clearInterval(timer);timer=null}}

function poll(){
  if(busy)return;
  busy=true;
  var ctl=typeof AbortController==='function'?new AbortController():null;
  var cut=setTimeout(function(){if(ctl)ctl.abort()},8000);
  fetch('/api/monitor',{method:'GET',headers:{'X-OPOS-Token':token},cache:'no-store',credentials:'same-origin',signal:ctl?ctl.signal:undefined})
    .then(function(r){
      if(r.status===403)throw new Error('stale');
      if(!r.ok)throw new Error('bad');
      return r.json().catch(function(){throw new Error('bad')});
    })
    .then(render)
    .catch(function(e){
      var m=e&&e.message;
      if(m==='stale'){stop();failed('This page is out of date. Tap Back to the phone, then open Inference again.')}
      else if(m==='bad')failed('OPOS answered with an error.');
      else failed('Could not reach OPOS.');
    })
    .then(function(){clearTimeout(cut);busy=false});
}

function tick(){
  if(document.visibilityState==='hidden')return;
  if(busy){if(drawn&&now()-okAt>6000)failed('OPOS is slow to answer.');return}
  poll();
}

if(!token){
  status.textContent='This page has no access token. Tap Back to the phone, then open Inference again.';
}else{
  document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible'&&timer!==null)poll()});
  timer=setInterval(tick,2000);
  poll();
}
})();
