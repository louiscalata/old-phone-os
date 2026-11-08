barC=G('demo').hidden?'':s.clock;
if(s.battery){
const m=s.battery.meter||{};
barM=m;barPaint();
const title=G('sheet-title'),low=s.battery.percent<=15;title.textContent=s.battery.percent+'%'+(low?' LOW':'');title.setAttribute('aria-label',(m.label||'')+(low?', low':''));title.dataset.tone=m.tone||'normal';
G('time-left').textContent=s.battery.time_left||'';
{const l=s.lean||{};document.getElementById('lean-line').textContent=(Number.isFinite(l.opos_mb)&&Number.isFinite(l.free_mb))?`OPOS process uses ${l.opos_mb} MB \u00b7 ${l.free_mb} MB available`:'Memory figures unavailable on this device'}
G('headline').textContent=s.battery.headline||'';
G('goal-note').textContent=s.battery.goal_note||'';
if(document.activeElement!==G('goal')){G('goal').value=s.battery.goal||''}
