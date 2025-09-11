(function(){
  const $ = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>[...r.querySelectorAll(s)];
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY=v=>"¥"+Math.round(v).toLocaleString("ja-JP");

  // LIVE clock
  function tickLive(){
    const el=$('#liveTs'); if(!el) return;
    const d=new Date();
    const hh=String(d.getHours()).padStart(2,'0');
    const mm=String(d.getMinutes()).padStart(2,'0');
    const ss=String(d.getSeconds()).padStart(2,'0');
    el.textContent=`${hh}:${mm}:${ss}`;
  }

  // number animation
  function animateNumber(el,to,dur=700){
    if(!el) return;
    const reduce=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduce){ el.textContent=fmtJPY(to); return; }
    const from=parseFloat(el.dataset.value||to)||0;
    const start=performance.now();
    function step(now){
      const t=clamp((now-start)/dur,0,1);
      const val=from+(to-from)*(1-Math.pow(1-t,3));
      el.textContent=fmtJPY(val);
      if(t<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    el.dataset.value=to;
  }

  // 横比率バー
  function renderStackBars(el){
    if(!el) return;
    const s=parseFloat(el.dataset.spot||"0");
    const m=parseFloat(el.dataset.margin||"0");
    const c=parseFloat(el.dataset.cash||"0");
    const total=Math.max(1,s+m+c);
    const p=v=>Math.max(2,(v/total)*100);
    el.innerHTML=`
      <span style="width:${p(s)}%;background:var(--primary)"></span>
      <span style="width:${p(m)}%;background:#ff8a5b"></span>
      <span style="width:${p(c)}%;background:var(--accent)"></span>`;
  }

  // PnL 色
  function paintPnL(){
    $$('.pnl').forEach(el=>{
      const s=parseFloat(el.dataset.sign||"0");
      el.classList.toggle('pos', s>=0);
      el.classList.toggle('neg', s<0);
    });
  }

  // details summary text
  function setupBreakdown(){
    const d=$('#breakdown'); if(!d) return;
    const s=d.querySelector('.summary-btn');
    const set=()=>{ s.textContent=d.open?'内訳を隠す':'内訳を表示'; };
    d.addEventListener('toggle',set); set();
  }

  // 利益率ゲージ（現物）
  function renderSpotRate(){
    const meter=$('#spotRate'); if(!meter) return;
    const mv=parseFloat(meter.dataset.mv||"0");
    const upl=parseFloat(meter.dataset.upl||"0");
    const totalCost=mv - upl;
    let rate=0;
    if(totalCost>0) rate=(upl/totalCost)*100;
    // 収まりの良い範囲にクランプ（-100%〜+100%）
    const clamped=clamp(rate,-100,100);
    meter.querySelector('.meter-fill').style.width = `${Math.abs(clamped)}%`;
    meter.querySelector('.meter-fill').style.background =
      clamped>=0 ? 'linear-gradient(90deg,var(--success),var(--primary))'
                 : 'linear-gradient(90deg,var(--danger),#ff9aa6)';
    meter.querySelector('.meter-label').textContent =
      `${clamped>=0?'+':''}${clamped.toFixed(1)}%`;
  }

  // ミニ損益スパーク（信用）
  function renderMiniSpark(el){
    if(!el) return;
    let raw=(el.dataset.points||'').trim();
    // 履歴が無ければ 0, 現在損益 の2点で描く
    if(!raw){
      const cur=parseFloat(el.dataset.fallback||'0')||0;
      raw=`0,${cur}`;
    }
    const vals=raw.split(',').map(Number).filter(v=>!Number.isNaN(v));
    if(vals.length<2){ el.textContent='—'; return; }
    const w=el.clientWidth||320, h=el.clientHeight||54, pad=6;
    const min=Math.min(...vals), max=Math.max(...vals);
    const x=i=>pad+(w-pad*2)*(i/(vals.length-1));
    const y=v=>max===min? h/2 : pad + (1-((v-min)/(max-min)))*(h-pad*2);
    const pts=vals.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    el.innerHTML=`
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
        <polyline points="${pts}" fill="none" stroke="var(--primary)" stroke-width="3"/>
        <polyline points="${pts}" fill="none" stroke="rgba(110,168,255,.35)" stroke-width="7" opacity=".35"/>
      </svg>`;
  }

  function init(){
    tickLive(); setInterval(tickLive,1000);

    const totalEl=$('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    renderStackBars($('#stackBars'));
    paintPnL();
    setupBreakdown();
    renderSpotRate();
    renderMiniSpark($('#marginSpark'));

    // リサイズ時にスパークを再描画
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t=setTimeout(()=>renderMiniSpark($('#marginSpark')),120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();