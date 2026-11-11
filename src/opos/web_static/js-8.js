async function toggleVis(){render(await api({action:'visible',visible:!vis}));if(vis)G('ask').scrollIntoView({block:'nearest'})}
var G=i=>document.getElementById(i);
G('battery').oninput=e=>G('battery-val').textContent=Math.round(parseFloat(e.target.value))+'%';G('battery').onchange=e=>api({action:'battery',level:parseFloat(e.target.value)}).then(render);
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>go(b.dataset.go));G('simbtn').onclick=toggleSim;G('visbtn').onclick=toggleVis;G('helpertoggle').onclick=toggleVis;
G('temp').oninput=e=>G('temp-val').textContent=e.target.value+'°C';
G('temp').onchange=e=>api({action:'temp',temp:parseFloat(e.target.value)}).then(render);
G('charge').onchange=e=>api({action:'charge',on:e.target.checked}).then(render);
G('goal').onchange=e=>api({action:'goal',time:e.target.value}).then(render);
G('goal-clear').onclick=()=>{G('goal').value='';api({action:'goal',time:''}).then(render)};
G('number').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();G('msg').focus()}});
G('msg').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!e.repeat){e.preventDefault();go('send')}});
G('q').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!e.repeat&&!e.defaultPrevented){const a=G('ask');if(a&&a.dataset.hs==='1')return;e.preventDefault();G('askbtn').click()}});
let pollMs=1000,pollTimer=null,pollDue=0,pollAt=performance.now();
function armPoll(ms){clearTimeout(pollTimer);pollDue=Date.now()+ms;pollTimer=setTimeout(pollTick,ms)}
const renderShown=render;
let seen='';const stateKey=s=>JSON.stringify(s,(k,v)=>k==='lean'?0:v)+navigator.onLine;
render=async s=>{seen='';await renderShown(s);if(s&&!s.error){seen=stateKey(s);pollMs=s.poll_ms||1000;if(pollTimer!==null&&Date.now()+pollMs<pollDue)armPoll(pollMs)}};
['load','click','keydown','input'].forEach(t=>addEventListener(t,()=>seen='',1));
var oposPoll=()=>0;
async function pollTick(){pollTimer=null;const n=performance.now(),real_s=(n-pollAt)/1000;pollAt=n;try{if(running)await render(await api({action:'tick',real_s}));else{const s=await fetchState();if(!s||s.error||stateKey(s)!==seen)await render(s);oposPoll(s&&!s.error?1:0)}}catch(e){running||oposPoll(0)}finally{if(pollTimer===null)armPoll(pollMs)}}
armPoll(pollMs);
function fetchState(){return fetch('/api/state',{headers:{'X-OPOS-Token':document.querySelector('[name=opos-token]').content},signal:AbortSignal.timeout?.(8000)}).then(r=>r.ok===false?{json:()=>r.json().then(s=>s?.error?s:null)}:r).then(r=>r.json())}
fetchState().then(s=>{if(!s||s.error)throw new Error('bad state');return render(s)}).catch(()=>{G('screen').textContent='Could not reach Old Phone OS Interface. Reload the page in a few seconds.'});
