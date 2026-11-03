function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
async function render(s){
if(!s)return;
if(s.error){
const errorNotice=document.getElementById('notice');
if(errorNotice){
errorNotice.textContent=s.error==='forbidden'?'This page is out of date. Reload it.':s.error==='offline'?'Phone service not reachable. Reload the page.':s.error==='too many requests'?'Too many taps. Wait a few seconds, then try again.':'That did not work. Reload the page, then try again.';
errorNotice.hidden=false;
errorNotice.dataset.tone='';
}
return;
}
running=!!s.running;vis=!!s.helper_visible;
const sc=G('screen');
if(s.screen)sc.innerHTML=s.screen.map(esc).join('<br>');
if(typeof s.brightness==='number'&&isFinite(s.brightness)&&s.brightness>=0&&s.brightness<=100){sc.style.filter='brightness('+(Math.max(s.brightness,60)/100)+')'}else{sc.style.filter=''}
G('status').textContent=(s.in_call?'[call] ':'')+s.status;
