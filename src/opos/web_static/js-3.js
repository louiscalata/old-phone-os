let barP;G('meter-hit').addEventListener('pointerdown',e=>{if(dragging)e.stopImmediatePropagation();else if(!sheetOpen)barP=e.pointerId},true);
function sheetDown(e){if(!sheetOpen||dragging)return;barP=e.pointerId;sheetDrag=dragging=true;startY=e.clientY;sheetH=sheetEl.offsetHeight||innerHeight;dragEl=e.currentTarget;sheetEl.style.transition='none';try{dragEl.setPointerCapture(e.pointerId)}catch(_){}}
[handleEl,titleEl,timeEl,gripEl].forEach(el=>el.addEventListener('pointerdown',sheetDown));
[handleEl,gripEl].forEach(el=>el.addEventListener('click',()=>{if(sheetOpen)closeSheet()}));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&sheetOpen)closeSheet()});
scrimEl.addEventListener('click',closeSheet);
dragOn('pointermove',e=>{if(hitDown&&!sheetOpen){const dy=e.clientY-hitY;if(dy>5||Math.abs(e.clientX-hitX)>8)hitMoved=true;const c=Math.min(Math.max(dy,0),sheetH||sheetEl.offsetHeight||innerHeight),t=sheetEl.style;t.visibility='visible';t.transition='none';t.transform='translateY(calc(-100% + '+c+'px))';return}if(!sheetDrag||!sheetOpen)return;const dy=e.clientY-startY;sheetEl.style.transform='translateY('+Math.min(0,Math.max(dy,-sheetH))+'px)'});
function endDrag(e){const dy=(e.clientY??startY)-startY;if(hitDown){if(dy>40&&!sheetOpen){suppressHitClick=true;openSheet()}else if(hitMoved||dy>5)suppressHitClick=true}else if(sheetDrag&&sheetOpen&&dy<-40)closeSheet();cancelDrag()}
function cancelDrag(){barP=null;hitDown=sheetDrag=dragging=hitMoved=false;sheetReset()}
function dragOn(t,f){window.addEventListener(t,e=>{if(e.pointerId===barP)f(e)})}
dragOn('pointerup',endDrag);
dragOn('pointercancel',cancelDrag);
dragOn('lostpointercapture',cancelDrag);
function barPaint(){const e={r:barR,v:barV}[barS.value],m=barM,h=parseInt(barC||barT.textContent.slice(-5)),n=barN.checked&&(h>=22||h<7),r=n?dim:barR.value,v=e==barR?r:e&&e.value;document.documentElement.style.filter=r<100?'brightness('+r/100+')':'';[barR,barV].forEach(e=>G(e.id+'-val').textContent=e.value+'%');G('night-note').textContent=n?dim+'% until 07:00':'';meterEl.dataset.caption=e==barR?'Brightness':e?'Volume':'';meterEl.dataset.tone=e?'':m&&m.tone||'normal';if(e||m){G('meter-fill').style.width=(e?v:m.width||0)+'%';meterEl.setAttribute('aria-label',(e?(e==barR?'Brightness ':'Simulated volume ')+v+' percent':m.label||'')+', pull down for details')}}
const dim=40,barS=G('topbar'),barR=G('bright'),barV=G('vol'),barN=G('night'),barT=G('sb-time');let barC='';[[barS,'opos_bar'],[barR,'opos_bright'],[barV,'opos_vol'],[barN,'opos_night']].forEach(([e,k])=>{const p=e==barN?'checked':'value';try{const v=localStorage.getItem(k);e[p]=e==barN?v==='true':v||e[p]}catch(_){}e.addEventListener('input',barPaint);e.addEventListener('change',()=>{try{localStorage.setItem(k,e[p])}catch(_){}})});barS.value||(barS.value='b');barPaint();new MutationObserver(barPaint).observe(barT,{childList:true});
async function api(o){try{const r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':document.querySelector('meta[name=opos-token]').content},body:JSON.stringify(o)});return await r.json()}catch(_){return{error:'offline'}}}
