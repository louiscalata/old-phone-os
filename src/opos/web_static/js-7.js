const pl=G('paused-list'),ps=s.battery.paused||[],pk=JSON.stringify(ps.map(p=>[p.id||p,p.name||p,p.reason||'',!!p.held]));if(pl._paused!==pk){pl._paused=pk;const po=new Set([...pl.querySelectorAll('details[open]')].map(d=>d.dataset.n));pl.innerHTML='';
ps.forEach(p=>{const row=E('div');row.className='paused-row';row.innerHTML=`<details data-n="${esc(p.id||p)}"${po.has(String(p.id||p))?' open':''}><summary><span>${esc(p.name||p)}</span></summary><p>${esc(p.reason||'')}</p></details>${p.held?'':`<button aria-label="Turn ${esc(p.name||p)} back on">Undo</button>`}`;const b=row.querySelector('button');if(b)b.onclick=()=>api({action:'undo',name:p.id||p}).then(render);pl.appendChild(row)});}
const poly=document.querySelector('#history polyline');const hist=s.battery.history||[];if(hist.length>1){let lo=Math.min(...hist),hi=Math.max(...hist);if(hi-lo<10){const mid=(hi+lo)/2;lo=mid-5;hi=mid+5}const pts=hist.map((v,i)=>`${(i/(hist.length-1))*100},${56-(v-lo)/(hi-lo)*52}`).join(' ');poly.setAttribute('points',pts)}else if(hist.length===1){poly.setAttribute('points','0,30 100,30')}else{poly.setAttribute('points','')}
sync(G('features'),(s.battery.features||[]).map(f=>`<div class="feature-row"><span>${esc(f.name)}</span><div class="feature-bar"><div class="feature-fill" style="width:${f.share||0}%"></div></div><span>${(f.per_hour||0)}%/h</span></div>`).join(''));
const why=G('why');if(vis&&s.battery.why){why.textContent=s.battery.why}else{why.textContent=''}
sync(G('events'),(s.battery.events||[]).map(e=>`<li>${esc(e)}</li>`).join(''));
}
}
async function go(a){if(a==='receive'&&!G('msg').value.trim()){showNotice('Type the incoming text first.','messages');G('msg').focus();return}render(await api({action:a,number:G('number').value,text:G('msg').value}))}
async function toggleSim(){render(await api({action:'sim',running:!running,speed:60}))}
