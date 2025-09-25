/* ===== swipe actions ===== */
(function(){
  const rows=document.querySelectorAll('[data-swipe]');
  const openW = a => parseFloat(getComputedStyle(a).getPropertyValue('--open-w'))||220;
  const closeOthers = except=>{
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if(r===except) return;
      r.classList.remove('is-open');
      const a=r.querySelector('.actions'); a.style.right=(-openW(a))+'px'; a.style.pointerEvents='none';
    });
  };

  rows.forEach(row=>{
    const a=row.querySelector('.actions');
    const track=row.querySelector('.track');
    const btn=row.querySelector('[data-action="detail"]');
    const OPEN=openW(a);
    a.style.right=(-OPEN)+'px'; a.style.pointerEvents='none';

    let sx=0,sy=0,drag=false,horiz=false,base=-OPEN;

    const setR=v=>a.style.right=v+'px';
    const start=e=>{
      if(e.target.closest('.actions')) return;
      const t=e.touches?e.touches[0]:e; sx=t.clientX; sy=t.clientY;
      drag=true; horiz=false; base=row.classList.contains('is-open')?0:-OPEN;
      a.style.transition='none'; closeOthers(row);
    };
    const move=e=>{
      if(!drag) return;
      const t=e.touches?e.touches[0]:e; const dx=t.clientX-sx, dy=t.clientY-sy;
      if(!horiz){ if(Math.abs(dx)<8) return; if(Math.abs(dx)>Math.abs(dy)) horiz=true; else {drag=false;a.style.transition='';return;} }
      if(e.cancelable) e.preventDefault();
      let nr=base-dx; if(nr>0) nr=0; if(nr<-OPEN) nr=-OPEN; setR(nr);
    };
    const end=()=>{
      if(!drag) return; drag=false; a.style.transition='right .18s ease-out';
      const cur=parseFloat(getComputedStyle(a).right)||-OPEN;
      const willOpen=(cur>-OPEN/2);
      if(willOpen){ row.classList.add('is-open'); setR(0); a.style.pointerEvents='auto'; }
      else{ row.classList.remove('is-open'); setR(-OPEN); a.style.pointerEvents='none'; }
    };

    row.addEventListener('touchstart',start,{passive:true});
    row.addEventListener('touchmove',move,{passive:false});
    row.addEventListener('touchend',end);

    a.addEventListener('click',e=>e.stopPropagation());
    btn.addEventListener('click',e=>{
      e.stopPropagation();
      row.classList.toggle('show-detail');
      row.classList.remove('is-open'); setR(-OPEN); a.style.pointerEvents='none';
    });
    track.addEventListener('click',()=>{
      row.classList.remove('show-detail');
      if(row.classList.contains('is-open')){ row.classList.remove('is-open'); setR(-OPEN); a.style.pointerEvents='none'; }
    });
  });

  document.addEventListener('click',e=>{
    if(e.target.closest('.actions')) return;
    if(!e.target.closest('[data-swipe]')) closeOthers(null);
  });
})();

/* ===== spark drawing (color by pnl%) ===== */
(function(){
  function draw(svg, pts, color){
    if(!pts || !pts.length){ svg.replaceChildren(); return; }
    let min=Math.min(...pts), max=Math.max(...pts);
    if(max===min){ max=min+1e-6; }
    const w=svg.viewBox.baseVal.width||110, h=svg.viewBox.baseVal.height||32, pad=2;
    const step=(w-pad*2)/Math.max(pts.length-1,1);
    const path=pts.map((v,i)=>{
      const x=pad+i*step;
      const y=h-pad-((v-min)/(max-min))*(h-pad*2);
      return `${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    svg.innerHTML=`<path d="${path}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" opacity="0.95"/>`;
  }
  document.querySelectorAll('svg.spark[data-spark]').forEach(svg=>{
    try{
      const arr=JSON.parse(svg.getAttribute('data-spark')||'[]');
      const rate=parseFloat(svg.getAttribute('data-rate')||'0');
      draw(svg, arr, (isFinite(rate)&&rate<0)?'#ef4444':'#22c55e');
    }catch(_){ /* noop */ }
  });
})();