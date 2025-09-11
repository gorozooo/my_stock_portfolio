(function(){
  const $ = (sel, root=document)=>root.querySelector(sel);
  const $$ = (sel, root=document)=>[...root.querySelectorAll(sel)];

  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY = v => "¥"+Math.round(v).toLocaleString("ja-JP");

  // ライブ時刻
  function tickLive(){
    const el = $('#liveTs'); if(!el) return;
    const d = new Date();
    const hh = d.getHours().toString().padStart(2,'0');
    const mm = d.getMinutes().toString().padStart(2,'0');
    const ss = d.getSeconds().toString().padStart(2,'0');
    el.textContent = `${hh}:${mm}:${ss}`;
  }

  // 総資産の数値アニメ（控えめ）
  function animateNumber(el, to, dur=700){
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduce){ el.textContent = fmtJPY(to); return; }
    const from = parseFloat(el.dataset.value||to) || 0;
    const start = performance.now();
    function step(now){
      const t = clamp((now-start)/dur, 0, 1);
      const val = from + (to-from) * (1 - Math.pow(1-t, 3));
      el.textContent = fmtJPY(val);
      if(t<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    el.dataset.value = to;
  }

  // スパーク（総資産の小チャート）
  function renderSpark(el){
    if(!el) return;
    const raw = (el.dataset.points||'').trim();
    const vals = raw.split(',').map(Number).filter(v=>!Number.isNaN(v));
    if(vals.length < 2){ el.textContent = 'データなし'; return; }
    const w = el.clientWidth || 320, h = el.clientHeight || 60, pad = 6;
    const min = Math.min(...vals), max = Math.max(...vals);
    const x = i => pad + (w-pad*2) * (i/(vals.length-1));
    const y = v => max===min ? h/2 : pad + (1-((v-min)/(max-min))) * (h-pad*2);
    const pts = vals.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    el.innerHTML = `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
        <defs>
          <linearGradient id="g1" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="var(--primary)"/>
            <stop offset="100%" stop-color="var(--accent)"/>
          </linearGradient>
          <filter id="glow"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        <polyline points="${pts}" fill="none" stroke="url(#g1)" stroke-width="3" filter="url(#glow)"/>
      </svg>`;
  }

  // ポート比率（横バー）
  function renderStackBars(el){
    if(!el) return;
    const spot = parseFloat(el.dataset.spot||"0");
    const margin = parseFloat(el.dataset.margin||"0");
    const cash = parseFloat(el.dataset.cash||"0");
    const total = Math.max(1, spot + margin + cash);
    const p = v => Math.max(2, (v/total)*100);
    el.innerHTML = `
      <span style="width:${p(spot)}%;background:var(--primary)"></span>
      <span style="width:${p(margin)}%;background:#ff8a5b"></span>
      <span style="width:${p(cash)}%;background:var(--accent)"></span>`;
  }

  // PnL 色付け
  function paintPnL(){
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      el.classList.toggle('pos', s>=0);
      el.classList.toggle('neg', s<0);
    });
  }

  // details の summary テキスト切り替え
  function setupBreakdown(){
    const d = $('#breakdown'); if(!d) return;
    const sum = d.querySelector('.summary-btn');
    const set = () => { sum.textContent = d.open ? '内訳を隠す' : '内訳を表示'; };
    d.addEventListener('toggle', set);
    set();
  }

  function init(){
    // ライブ時刻
    tickLive();
    setInterval(tickLive, 1000);

    // 総資産アニメ
    const totalEl = $('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    // スパーク
    renderSpark($('#assetSpark'));

    // 比率バー
    renderStackBars($('#stackBars'));

    // PnL 色
    paintPnL();

    // 内訳 details
    setupBreakdown();

    // リサイズでスパーク再描画
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t = setTimeout(()=>renderSpark($('#assetSpark')), 120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();