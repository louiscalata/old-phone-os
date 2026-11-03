async function toggleVis(){render(await api({action:'visible',visible:!vis}));if(vis)document.getElementById('ask').scrollIntoView({block:'nearest'})}
var G=i=>document.getElementById(i);
document.getElementById('battery').oninput=e=>document.getElementById('battery-val').textContent=Math.round(parseFloat(e.target.value))+'%';document.getElementById('battery').onchange=e=>api({action:'battery',level:parseFloat(e.target.value)}).then(render);
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>go(b.dataset.go));G('simbtn').onclick=toggleSim;G('visbtn').onclick=toggleVis;document.getElementById('helpertoggle').onclick=toggleVis;
G('temp').onchange=e=>api({action:'temp',temp:parseFloat(e.target.value)}).then(render);
G('charge').onchange=e=>api({action:'charge',on:e.target.checked}).then(render);
G('goal').onchange=e=>api({action:'goal',time:e.target.value}).then(render);
document.getElementById('goal-clear').onclick=()=>{G('goal').value='';api({action:'goal',time:''}).then(render)};
document.getElementById('number').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();document.getElementById('msg').focus()}});
document.getElementById('msg').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!e.repeat){e.preventDefault();go('send')}});
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!e.repeat&&!e.defaultPrevented){const a=document.getElementById('ask');if(a&&a.dataset.hs==='1')return;e.preventDefault();document.getElementById('askbtn').click()}});
let pollMs=1000,pollTimer=null,pollDue=0;
function armPoll(ms){clearTimeout(pollTimer);pollDue=Date.now()+ms;pollTimer=setTimeout(pollTick,ms)}
const renderShown=render;
render=async s=>{await renderShown(s);if(s&&!s.error){pollMs=s.poll_ms||1000;if(pollTimer!==null&&Date.now()+pollMs<pollDue)armPoll(pollMs)}};
async function pollTick(){pollTimer=null;try{if(running)await render(await api({action:'tick',real_s:1}))}catch(e){await renderShown({error:'request failed'})}finally{if(pollTimer===null)armPoll(pollMs)}}
armPoll(pollMs);
fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content}}).then(r=>r.json()).then(s=>{if(!s||s.error)throw new Error('bad state');return render(s)}).catch(()=>{document.getElementById('screen').textContent='Could not reach Old Phone OS Interface. Reload the page in a few seconds.'});
