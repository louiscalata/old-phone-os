const pl=G('paused-list'),ps=s.battery.paused||[],pk=JSON.stringify(ps.map(p=>[p.id||p,p.name||p,p.reason||'']));if(pl._paused!==pk){pl._paused=pk;const po=new Set([...pl.querySelectorAll('details[open]')].map(d=>d.dataset.n));pl.innerHTML='';
ps.forEach(p=>{const row=E('div');row.className='paused-row';row.innerHTML=`<details data-n="${esc(p.id||p)}"${po.has(String(p.id||p))?' open':''}><summary><span>${esc(p.name||p)}</span></summary><p>${esc(p.reason||'')}</p></details><button aria-label="Turn ${esc(p.name||p)} back on">Undo</button>`;row.querySelector('button').onclick=()=>api({action:'undo',name:p.id||p}).then(render);pl.appendChild(row)});}
const poly=document.querySelector('#history polyline');const hist=s.battery.history||[];if(hist.length>1){let lo=Math.min(...hist),hi=Math.max(...hist);if(hi-lo<10){const mid=(hi+lo)/2;lo=mid-5;hi=mid+5}const pts=hist.map((v,i)=>`${(i/(hist.length-1))*100},${56-(v-lo)/(hi-lo)*52}`).join(' ');poly.setAttribute('points',pts)}else if(hist.length===1){const v=hist[0];let lo=v-5,hi=v+5;const y=56-(v-lo)/(hi-lo)*52;poly.setAttribute('points',`0,${y} 100,${y}`)}else{poly.setAttribute('points','')}
const feats=G('features');feats.innerHTML='';(s.battery.features||[]).forEach(f=>{const r=E('div');r.className='feature-row';r.innerHTML=`<span>${esc(f.name)}</span><div class="feature-bar"><div class="feature-fill" style="width:${f.share||0}%"></div></div><span>${(f.per_hour||0)}%/h</span>`;feats.appendChild(r)});
const why=G('why');if(vis&&s.battery.why){why.textContent=s.battery.why}else{why.textContent=''}
const ev=G('events');ev.innerHTML='';(s.battery.events||[]).forEach(e=>{const li=E('li');li.textContent=e;ev.appendChild(li)});
}
}
async function go(a){render(await api({action:a,number:G('number').value,text:G('msg').value}))}
async function toggleSim(){render(await api({action:'sim',running:!running,speed:60}))}
