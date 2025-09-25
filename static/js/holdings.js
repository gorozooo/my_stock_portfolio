/* === スワイプ＋詳細トグル（アクションを“固定”） === */
(function(){
  const rows=document.querySelectorAll('[data-swipe]');
  function getOpenW(a){return parseFloat(getComputedStyle(a).getPropertyValue('--open-w'))||220}
  function closeAll(except){
    document.querySelectorAll('[data-swipe].is-open').forEach(r=>{
      if(r===except) return;
      r.classList.remove('is-open');
      const a=r.querySelector('.actions'); a.style.right=(-getOpenW(a))+'px'; a.style.pointerEvents='none';
    });
  }

  rows.forEach(row=>{
    const a=row.querySelector('.actions');
    const btn=row.querySelector('[data-action="detail"]');
    const track=row.querySelector('.track');
    const OPEN=getOpenW(a);
    a.style.right=(-OPEN)+'px'; a.style.pointerEvents='none';

    let sx=0,sy=0,drag=false,horiz=false,baseR=-OPEN;

    const setR=px=>a.style.right=px+'px';

    const start=e=>{
      if(e.target.closest('.actions')) return; // パネル上はドラッグ開始しない（クリック可）
      const t=e.touches?e.touches[0]:e;
      sx=t.clientX; sy=t.clientY; drag=true; horiz=false;
      baseR=row.classList.contains('is-open')?0:-OPEN;
      a.style.transition='none';
      if(document.querySelector('[data-swipe].is-open') && !row.classList.contains('is-open')) closeAll(row);
    };

    const move=e=>{
      if(!drag) return;
      const t=e.touches?e.touches[0]:e;
      const dx=t.clientX-sx, dy=t.clientY-sy;
      if(!horiz){
        if(Math.abs(dx)<8) return;
        if(Math.abs(dx)>Math.abs(dy)) horiz=true; else {drag=false;a.style.transition='';return;}
      }
      if(e.cancelable) e.preventDefault();
      let nr=baseR-dx; if(nr>0) nr=0; if(nr<-OPEN) nr=-OPEN; setR(nr);
    };

    const end=()=>{
      if(!drag) return;
      drag=false; a.style.transition='right .18s ease-out';
      const cur=parseFloat(getComputedStyle(a).right)||-OPEN;
      const open=(cur>-OPEN/2);
      if(open){row.classList.add('is-open'); setR(0); a.style.pointerEvents='auto';}
      else{row.classList.remove('is-open'); setR(-OPEN); a.style.pointerEvents='none';}
    };

    row.addEventListener('touchstart',start,{passive:true});
    row.addEventListener('touchmove',move,{passive:false});
    row.addEventListener('touchend',end);

    // アクション内クリックは安定化のため stop
    a.addEventListener('click', e=>e.stopPropagation());

    // 詳細トグル：パネルは閉じる（誤タップ防止）
    btn.addEventListener('click', e=>{
      e.stopPropagation();
      row.classList.toggle('show-detail');
      row.classList.remove('is-open'); setR(-OPEN); a.style.pointerEvents='none';
    });

    // 本体タップでは“閉じない”＝固定（ユーザーが再スワイプ or 外側タップで閉じる）
    track.addEventListener('click', e=>{
      if(e.target.closest('.actions,.item,a,button')) return;
      if(row.classList.contains('show-detail')) row.classList.remove('show-detail');
      // is-open は維持（固定）
    });
  });

  // 外側タップで開いているパネルを閉じる
  document.addEventListener('click', e=>{
    if(e.target.closest('.actions')) return;
    if(!e.target.closest('[data-swipe]')) closeAll(null);
  });
})();

/* === スパーク描画（損益率で色切替） === */
function drawSpark(svg, data, rate){
  try{
    if(!data || !data.length){ svg.replaceChildren(); return; }
    let min=Math.min(...data), max=Math.max(...data);
    if(min===max){ min-=1e-6; max+=1e-6; }
    const pts=data.map((v,i)=>{
      const x=(i/(data.length-1))*100;
      const y=(1-(v-min)/(max-min))*28;
      return `${x},${y}`;
    }).join(' ');
    const col = (parseFloat(rate)||0) < 0 ? '#ef4444' : '#22c55e';
    svg.innerHTML=`<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linecap="round" opacity="0.95"/>`;
  }catch(_){ svg.replaceChildren(); }
}
(function(){
  document.querySelectorAll('svg.spark[data-spark]').forEach(svg=>{
    let arr=[]; try{ const raw=svg.getAttribute('data-spark'); arr=Array.isArray(raw)?raw:JSON.parse(raw||'[]'); }catch(_){}
    const rate = svg.getAttribute('data-rate') || '0';
    drawSpark(svg, arr, rate);
  });
})();