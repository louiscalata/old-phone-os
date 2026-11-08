const _n=G('notice');if(_n){showNotice(s.notice||'',s.notice_app||'');_n.dataset.tone=s.in_call?'call':''}
G('simbtn').textContent=running?'Pause simulation':'Start simulation';
document.getElementById('visbtn').textContent=vis?'Hide helper':'Show helper';
const helperToggle=document.getElementById('helpertoggle');
if(helperToggle){helperToggle.textContent=vis?'Hide helper':'Show helper';}
const h=G('helper');
if(s.helper){h.hidden=false;h.textContent='Helper\nMode: '+s.helper.mode+'\nAllowed: '+s.helper.allowed.join(', ')+'\nPaused: '+s.helper.paused.join(', ')+'\nHours left: '+(s.helper.hours_left===null?'not known yet':s.helper.hours_left)+'\nReserve: '+s.helper.reserve+'\nSource: '+s.helper.source+'\nHelper set to: '+s.helper.engine;const b=E('button');b.textContent='Why?';b.onclick=()=>helperCall({action:'explain'},document.getElementById('answer')).then(r=>{if(!r)return;const ans=G('answer');ans.textContent='';if(r.error){ans.textContent=r.error==='helper hidden'?'Tap Show helper first':r.error;return}if(r.ok===false){ans.textContent=r.text+' (Rules text. The helper did not answer this request.)'}else{ans.textContent=r.text+' ('+r.engine+', '+r.where+')'}});h.appendChild(document.createElement('br'));h.appendChild(b)}else{h.hidden=true;h.textContent=''}G('ask').hidden=!s.helper;
G('contacts').innerHTML='<b>Example contacts</b><br>'+((s.contacts||[]).map(c=>`<button data-n="${esc(c.number)}">${esc(c.name)} ${esc(c.number)}</button>`).join(' ')||'No contacts yet.');
document.querySelectorAll('#contacts button').forEach(b=>b.onclick=()=>{G('number').value=b.dataset.n});
document.getElementById('threads').innerHTML='<b>Threads</b><br>'+((s.threads||[]).length===0?'No messages yet. Type a number and a message, then tap Send.':(s.threads||[]).map(t=>{const n=t.number||'',nm=t.name||n,tx=t.preview||t.last||t.text||'';return`<button data-n="${esc(n)}">${esc(nm)} ${esc(tx)}</button>`}).join(' '));
