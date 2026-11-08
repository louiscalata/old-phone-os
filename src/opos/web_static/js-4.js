function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function healReload(){fetch('/',{cache:'no-store'}).then(r=>{if(!r.ok)throw 0;location.reload()}).catch(()=>showNotice('Still not reachable. Try again in a moment.'))}
const sync=(el,html,wire)=>{if(el._k!==html){el._k=html;el.innerHTML=html;wire&&wire(el)}};
async function render(s){
if(!s)return;
if(s.error){
const errorNotice=document.getElementById('notice');
if(errorNotice){
errorNotice.textContent=s.error==='forbidden'?'Restarting the screen. One moment.':s.error==='offline'?'Not updating. Values shown may be old. Tap Reload.':s.error==='too many requests'?'Too many taps. Wait a few seconds, then try again.':'That did not work. Try again.';
errorNotice.hidden=false;
errorNotice.dataset.tone='';
}
if(s.error==='forbidden')try{if(Date.now()-(sessionStorage.opos_h||0)>1e4){sessionStorage.opos_h=Date.now();setTimeout(healReload,500)}}catch(e){}
return;
}
running=!!s.running;vis=!!s.helper_visible;
const sc=G('screen');
if(s.screen)sc.innerHTML=s.screen.map(esc).join('<br>');
if(typeof s.brightness==='number'&&isFinite(s.brightness)&&s.brightness>=0&&s.brightness<=100){sc.style.filter='brightness('+(Math.max(s.brightness,60)/100)+')'}else{sc.style.filter=''}
G('status').textContent=(s.in_call?'[call] ':'')+s.status;
