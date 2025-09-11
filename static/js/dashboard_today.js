(function(){
  const $  = (s,r=document)=>r.querySelector(s);
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY=v=>"¥"+Math.round(v).toLocaleString("ja-JP");

  // LIVE時計
  function tickLive(){
    const el=$('#liveTs'); if(!el) return;
    const d=new Date();
    el.textContent=[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');
  }

  // 数値アニメ
  function animateNumber(el,to,dur=700){
    if(!el) return;
    const from=parseFloat(el.dataset.val||to)||0;
    const t0=performance.now();
    function step(t){
      const k=clamp((t-t0)/dur,0,1);
      const v=from+(to-from)*(1-Math.pow(1-k,3));
      el.textContent=fmtJPY(v);
      if(k<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    el.dataset.val=to;
  }

  // 今日の損益スパーク（実データ＋ノイズ）
  function renderSparkToday(el){
    if(!el) return;
    const base=parseFloat(el.dataset.base||'0')||0;
    // ベースを中心に軽く揺らす（見た目用）
    const N=48; // 1営業日のイメージ点数
    const pts=[];
    let v=base*0.2; // スタートは控えめ
    for(let i=0;i<N;i++){
      // ランダムウォーク + ベースへ収束
      const drift=(base - v)*0.05;
      const shock=(Math.random()-0.5)*base*0.02; // 2%幅の軽い揺れ
      v += drift + shock;
      pts.push(v);
    }
    const w=el.clientWidth||560, h=el.clientHeight||88, pad=10;
    const min=Math.min(...pts), max=Math.max(...pts);
    const x=i=>pad + (w-pad*2)*(i/(N-1));
    const y=val=> max===min ? h/2 : pad + (1-((val-min)/(max-min)))*(h-pad*2);
    const d=pts.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    el.innerHTML = `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
        <defs>
          <linearGradient id="g-a" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="var(--accent)"/>
            <stop offset="100%" stop-color="var(--primary)"/>
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="2.2" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        <polyline points="${d}" fill="none" stroke="url(#g-a)" stroke-width="3" filter="url(#glow)"/>
      </svg>`;
  }

  function init(){
    // 時計
    tickLive(); setInterval(tickLive,1000);

    // 今日の損益 数値アニメ
    const pnlEl=$('#pnlYen');
    if(pnlEl) animateNumber(pnlEl, parseFloat(pnlEl.dataset.val||'0'));

    // スパーク描画
    renderSparkToday($('#sparkToday'));

    // リサイズで再描画
    let tm; window.addEventListener('resize', ()=>{
      clearTimeout(tm);
      tm=setTimeout(()=>renderSparkToday($('#sparkToday')),120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();