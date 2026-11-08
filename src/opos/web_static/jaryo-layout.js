try{(function(){
window.jaryo=window.jaryo||{};
function $(i){return document.getElementById(i)}
function strip(list){
var old=list.querySelectorAll('li.jaryo-letter-head');
for(var i=0;i<old.length;i++){old[i].parentNode.removeChild(old[i]);}
}
function txt(li){var b=li.querySelector('button');return(b?b.textContent:'').trim();}
window.jaryo.apps={
strip:function(){try{var l=$('apps-list');if(l)strip(l);}catch(e){}},
build:function(){
try{
var list=$('apps-list');
if(!list)return;
strip(list);
var items=[],drop=[];
for(var i=0;i<list.children.length;i++){
var li=list.children[i];
if(li.tagName.toLowerCase()!=='li')continue;
var fb=li.firstElementChild||li.querySelector('button');
if(fb&&fb.dataset&&fb.dataset.scroll){drop.push(li);continue;}
items.push(li);
}
for(var i=0;i<drop.length;i++){list.removeChild(drop[i]);}
items.sort(function(a,b){
var ta=txt(a).toLowerCase(),tb=txt(b).toLowerCase();
return ta<tb?-1:ta>tb?1:0;
});
for(var i=0;i<items.length;i++){list.appendChild(items[i]);}
var lettersDiv=$('jaryo-letters');
if(!lettersDiv){
lettersDiv=document.createElement('div');
lettersDiv.id='jaryo-letters';
list.parentNode.insertBefore(lettersDiv,list);
}else{
lettersDiv.innerHTML='';
}
lettersDiv.hidden=!!list.hidden;
var prev=null;
for(var i=0;i<items.length;i++){
var li=items[i],t=txt(li);
if(!t||li.hidden)continue;
var letter=t.charAt(0).toUpperCase();
if(letter===prev)continue;
prev=letter;
var head=document.createElement('li');
head.className='jaryo-letter jaryo-letter-head';
head.setAttribute('aria-hidden','true');
head.id='jaryo-letter-'+letter;
head.textContent=letter;
list.insertBefore(head,li);
var b=document.createElement('button');
b.type='button';
b.textContent=letter;
b.setAttribute('aria-label','Jump to '+letter);
(function(letter){
b.addEventListener('click',function(){
try{
var h=$('jaryo-letter-'+letter);
if(!h)return;
var n=h.nextElementSibling,ab=n&&n.querySelector('button');
if(ab&&typeof ab.focus==='function'){try{ab.focus({preventScroll:true});}catch(e){ab.focus();}}
if(typeof h.scrollIntoView==='function'){try{h.scrollIntoView({block:'start'});}catch(e){h.scrollIntoView();}}
}catch(e){}
});
})(letter);
lettersDiv.appendChild(b);
}
}catch(e){}
}
};
function syncControls(){
var list=$('apps-list'),phone=$('phone');
if(!list||!phone)return;
var open=!list.hidden,back=$('back'),close=$('apps-close'),letters=$('jaryo-letters');
if(letters)letters.hidden=!open;
if(close)close.hidden=!open;
if(back)back.hidden=open||!phone.dataset.app||phone.dataset.app==='home';
}
function rebuild(){setTimeout(function(){try{window.jaryo.apps.build();syncControls();}catch(e){}},0);}
function wire(){
try{
var list=$('apps-list');
document.addEventListener('click',function(e){
try{
var t=e.target,a=t&&t.closest?t.closest('#allapps'):(t&&t.id==='allapps'?t:null);
if(!a)return;
window.jaryo.apps.strip();
rebuild();
}catch(err){}
},true);
document.addEventListener('click',function(){try{syncControls()}catch(e){}});
if(list&&typeof MutationObserver==='function'){
new MutationObserver(function(){
try{syncControls()}catch(e){}
}).observe(list,{attributes:true,attributeFilter:['hidden']});
}
window.jaryo.apps.build();
syncControls();
}catch(e){}
}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',wire);}else{wire();}
})()}catch(e){}
try{(function(){
(window.oposHooks=window.oposHooks||[]).push(function(s){try{
if(!s||!s.prefs)return;var h=document.getElementById('home');if(!h)return;
var layout=s.prefs.layout||{},sizes=localStorage.getItem('jaryo_tiles');
if(sizes===null){sizes=JSON.stringify(layout.sizes||{});localStorage.setItem('jaryo_tiles',sizes)}
try{sizes=JSON.parse(sizes)}catch(e){sizes=null}
if(!sizes||typeof sizes!=='object')sizes={};
h.querySelectorAll('button[data-open]').forEach(function(t){var id=t.dataset.open,v=sizes[id];
t.dataset.size=['small','medium','wide'].indexOf(v)>=0?v:(id==='phone'||id==='messages'?'medium':'small');
});
}catch(e){}});
})()}catch(e){}
