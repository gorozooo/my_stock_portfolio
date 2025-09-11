(function(){
  const $  = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>[...r.querySelectorAll(s)];
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY = v => "¥" + Math.round(v).toLocaleString("ja-JP");

  // ===== 背景：動くオーブ（HTMLに無ければ自動挿入） =====
  function mountBgLayer(){
    if($('.bg-layer')) return; // 既にある
    const layer = document.createElement('div');
    layer.className = 'bg-layer';
    layer.innerHTML = `
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
      <div class="orb orb-3"></div>
    `;
    document.body.appendChild(layer);
  }

  // ===== LIVE clock =====
  let liveTimer = null;
  function tickLive(){
    const el = $('#liveTs'); if(!el) return;
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    const ss = String(d.getSeconds()).padStart(2,'0');
    el.textContent = `${hh}:${mm}:${ss}`;
  }
  function startLive(){
    tickLive();
    if(liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(tickLive, 1000);
  }

  // ===== number animation（総資産） =====
  function animateNumber(el, to, dur=700){
    if(!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduce){ el.textContent = fmtJPY(to); return; }
    const from = parseFloat(el.dataset.value||to) || 0;
    const start = performance.now();
    el.classList.add('glow'); // うっすら光る
    function step(now){
      const t = clamp((now - start)/dur, 0, 1);
      const val = from + (to - from) * (1 - Math.pow(1 - t, 3));
      el.textContent = fmtJPY(val);
      if(t < 1) requestAnimationFrame(step);
      else setTimeout(()=>el.classList.remove('glow'), 400);
    }
    requestAnimationFrame(step);
    el.dataset.value = to;
  }

  // ===== 横比率バー（現物/信用/現金） =====
  function renderStackBars(el){
    if(!el) return;
    const s = parseFloat(el.dataset.spot||"0");
    const m = parseFloat(el.dataset.margin||"0");
    const c = parseFloat(el.dataset.cash||"0");
    const total = Math.max(1, s + m + c);
    const p = v => Math.max(2, (v/total)*100); // 最低2%で視認性確保
    el.innerHTML = `
      <span style="width:${p(s)}%;background:var(--primary)"></span>
      <span style="width:${p(m)}%;background:#ff8a5b"></span>
      <span style="width:${p(c)}%;background:var(--accent)"></span>`;
  }

  // ===== PnL 色分け =====
  function paintPnL(){
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      el.classList.toggle('pos', s >= 0);
      el.classList.toggle('neg', s < 0);
    });
  }

  // ===== 内訳（<details>）ラベル切替 =====
  function setupBreakdown(){
    const d = $('#breakdown'); if(!d) return;
    const s = d.querySelector('.summary-btn');
    const set = ()=>{ s.textContent = d.open ? '内訳を隠す' : '内訳を表示'; };
    d.addEventListener('toggle', set);
    set();
  }

  // ===== 利益率ゲージ（現物） =====
  function renderSpotRate(){
    const meter = $('#spotRate'); if(!meter) return;
    const mv  = parseFloat(meter.dataset.mv  || "0");
    const upl = parseFloat(meter.dataset.upl || "0");
    const totalCost = mv - upl; // mv - upl = 取得額
    let rate = 0;
    if(totalCost > 0) rate = (upl / totalCost) * 100;
    const clamped = clamp(rate, -100, 100); // -100%〜+100%
    const fill = meter.querySelector('.meter-fill');
    const label = meter.querySelector('.meter-label');
    // 幅・色
    fill.style.width = `${Math.abs(clamped)}%`;
    fill.style.background = clamped >= 0
      ? 'linear-gradient(90deg,var(--success),var(--primary))'
      : 'linear-gradient(90deg,var(--danger),#ff9aa6)';
    // ラベル
    label.textContent = `${clamped >= 0 ? '+' : ''}${clamped.toFixed(1)}%`;
    // アクセシビリティ
    meter.setAttribute('role','meter');
    meter.setAttribute('aria-valuemin','-100');
    meter.setAttribute('aria-valuemax','100');
    meter.setAttribute('aria-valuenow', String(clamped.toFixed(1)));
  }

  // ===== ミニ損益スパーク（信用） =====
  function renderMiniSpark(el){
    if(!el) return;
    let raw = (el.dataset.points || '').trim();
    // 履歴が無ければ 0, 現在損益 の2点
    if(!raw){
      const cur = parseFloat(el.dataset.fallback || '0') || 0;
      raw = `0,${cur}`;
    }
    const vals = raw.split(',').map(Number).filter(v => !Number.isNaN(v));
    if(vals.length < 2){ el.textContent = '—'; return; }
    const w = el.clientWidth || 320, h = el.clientHeight || 56, pad = 6;
    const min = Math.min(...vals), max = Math.max(...vals);
    const x = i => pad + (w - pad*2) * (i / (vals.length - 1));
    const y = v => max === min ? h/2 : pad + (1 - ((v - min)/(max - min))) * (h - pad*2);
    const pts = vals.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    el.innerHTML = `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
        <defs>
          <linearGradient id="ms-g" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="var(--accent)"/>
            <stop offset="100%" stop-color="var(--primary)"/>
          </linearGradient>
          <filter id="ms-glow">
            <feGaussianBlur stdDeviation="2.2" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        <polyline points="${pts}" fill="none" stroke="url(#ms-g)" stroke-width="3" filter="url(#ms-glow)"/>
      </svg>`;
  }

  // ===== リサイズ再描画（負荷低め） =====
  function setupResize(){
    let t;
    window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t = setTimeout(()=>{
        renderMiniSpark($('#marginSpark'));
      }, 120);
    });
  }

  // ===== Visibility（非表示時はLIVE停止） =====
  function setupVisibility(){
    document.addEventListener('visibilitychange', ()=>{
      if(document.hidden){
        if(liveTimer) clearInterval(liveTimer);
      }else{
        startLive();
      }
    });
  }

  // ===== 初期化 =====
  function init(){
    mountBgLayer();
    startLive();

    // 総資産アニメ
    const totalEl = $('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    // 比率・色
    renderStackBars($('#stackBars'));
    paintPnL();

    // 内訳まとめ
    setupBreakdown();

    // KPI: 現物ゲージ & 信用スパーク
    renderSpotRate();
    renderMiniSpark($('#marginSpark'));

    // 補助
    setupResize();
    setupVisibility();
  }

  document.addEventListener('DOMContentLoaded', init);
})();