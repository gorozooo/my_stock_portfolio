(function(){
  const $ = (sel, root=document)=>root.querySelector(sel);
  const $$ = (sel, root=document)=>[...root.querySelectorAll(sel)];

  // ===== ユーティリティ =====
  const fmtJPY = (v)=>"¥"+Math.round(v).toLocaleString("ja-JP");
  const clamp = (v, a, b)=>Math.max(a, Math.min(b, v));

  // ===== 数値アニメーション（低負荷） =====
  function animateNumber(el, to, dur=700){
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduce){ el.textContent = fmtJPY(to); return; }
    const from = parseFloat(el.dataset.value||to) || 0;
    const start = performance.now();
    function tick(now){
      const t = clamp((now - start) / dur, 0, 1);
      const val = from + (to - from) * (1 - Math.pow(1 - t, 3));
      el.textContent = fmtJPY(val);
      if(t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
    el.dataset.value = to;
  }

  // ===== ミニスパークライン（SVG） =====
  function renderSpark(el){
    const raw = (el?.dataset.points||"").trim();
    const vals = raw.split(",").map(Number).filter(v=>!Number.isNaN(v));
    if(!el || vals.length < 2){ el.textContent = "データなし"; return; }
    const w = el.clientWidth || 320, h = el.clientHeight || 68, pad = 6;
    const min = Math.min(...vals), max = Math.max(...vals);
    const x = i => pad + (w-pad*2) * (i/(vals.length-1));
    const y = v => max===min ? h/2 : pad + (1-((v-min)/(max-min))) * (h-pad*2);
    const pts = vals.map((v,i)=>`${x(i)},${y(v)}`).join(" ");
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

  // ===== 円リング（比率） =====
  function renderRing(el){
    const val = parseFloat(el.dataset.value||"0");
    const total = Math.max(1, parseFloat(el.dataset.total||"1"));
    const pct = clamp(val/total, 0, 1);
    const size = el.clientHeight || 90;
    const r = (size/2) - 8, c = Math.PI*2*r;
    const dash = c * pct, gap = c - dash;
    el.innerHTML = `
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
        <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="var(--line)" stroke-width="10" fill="none"/>
        <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="url(#gradRing)" stroke-linecap="round"
                stroke-dasharray="${dash} ${gap}" stroke-width="10" fill="none"
                transform="rotate(-90 ${size/2} ${size/2})"/>
        <defs>
          <linearGradient id="gradRing" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="var(--primary)"/>
            <stop offset="100%" stop-color="var(--accent)"/>
          </linearGradient>
        </defs>
        <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle"
              fill="var(--fg)" style="font-weight:800;font-size:12px">${Math.round(pct*100)}%</text>
      </svg>`;
  }

  // ===== 横スタックバー（現物/信用/現金） =====
  function renderStackBars(el){
    const spot = parseFloat(el.dataset.spot||"0");
    const margin = parseFloat(el.dataset.margin||"0");
    const cash = parseFloat(el.dataset.cash||"0");
    const total = Math.max(1, spot + margin + cash);
    const p = v => Math.max(2, (v/total)*100); // 最低2%は見せる
    el.innerHTML = `
      <span style="width:${p(spot)}%;background:var(--primary)"></span>
      <span style="width:${p(margin)}%;background:#ff8a5b"></span>
      <span style="width:${p(cash)}%;background:var(--accent)"></span>`;
  }

  // ===== リスクヒート（簡易スコア） =====
  function renderRisk(el){
    const cash = parseFloat($('#cashTotal')?.dataset.value||"0");
    const total = parseFloat($('#totalAssets')?.dataset.value||"0");
    const margin = parseFloat($('#marginMV')?.dataset.value||"0");
    // 現金多 → 低リスク, 信用多 → 高リスクの超単純スコア
    const cashPct = total ? cash/total : 0;
    const marginPct = total ? margin/total : 0;
    const score = clamp(50*(1-cashPct) + 50*(marginPct), 0, 100);
    // 条件に応じてグラデの位置を動かす
    el.style.background = `linear-gradient(90deg,
      #26d07c66 ${clamp(100-score,0,100)}%, 
      #ffd16666 ${clamp(100-score+10,0,100)}%, 
      #ff4d6766 ${clamp(100-score+20,0,100)}%)`;
  }

  // ===== ミニ予測（ダミー生成） =====
  function renderPreview(listEl){
    const ideas = [
      {k:"現金比率", v: ()=> {
        const c = parseFloat($('#cashTotal')?.dataset.value||"0");
        const t = parseFloat($('#totalAssets')?.dataset.value||"0") || 1;
        return Math.round(100*c/t) + "%";
      }},
      {k:"信用依存", v: ()=> {
        const m = parseFloat($('#marginMV')?.dataset.value||"0");
        const t = parseFloat($('#totalAssets')?.dataset.value||"0") || 1;
        return Math.round(100*m/t) + "%";
      }},
      {k:"想定ボラ", v: ()=> {
        const s = parseFloat($('#spotMV')?.dataset.value||"0");
        const m = parseFloat($('#marginMV')?.dataset.value||"0");
        const vol = Math.min(100, Math.round(10 + 0.000006*(s + 2*m)));
        return vol + "/100";
      }},
    ];
    listEl.innerHTML = ideas.map(i=>(
      `<div class="mini-item"><span>${i.k}</span><strong>${i.v()}</strong></div>`
    )).join("");
  }

  // ===== テーマ切替 =====
  function setupThemeToggle(btn){
    btn?.addEventListener('click', ()=>{
      document.documentElement.classList.toggle('light');
    });
  }

  // ===== イベント =====
  function setupReveal(btn, grid, deep){
    btn?.addEventListener('click', ()=>{
      const show = grid.hasAttribute('hidden');
      if(show){ grid.removeAttribute('hidden'); deep.style.display='block'; btn.textContent='内訳を隠す'; }
      else { grid.setAttribute('hidden',''); deep.style.display='none'; btn.textContent='内訳を展開'; }
    });
    $('#btnCollapse')?.addEventListener('click', ()=>{
      grid.setAttribute('hidden',''); deep.style.display='none'; $('#btnReveal').textContent='内訳を展開';
    });
  }

  // ===== 初期化 =====
  function init(){
    // 数値アニメ
    const totalEl = $('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    // スパーク
    renderSpark($('#assetSpark'));

    // リング＆ゲージ
    [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
    renderStackBars($('#stackBars'));
    renderRisk($('#riskHeat'));
    renderPreview($('#miniPreview'));

    // スパークデッキ（2枚）
    const deck = $('#sparkDeck');
    if(deck){
      const raw = (deck.dataset.points||'').trim();
      deck.innerHTML = `
        <div class="spark" data-points="${raw}"></div>
        <div class="spark" data-points="${raw}"></div>`;
      $$('.spark', deck).forEach(renderSpark);
    }

    setupReveal($('#btnReveal'), $('#kpiGrid'), $('#deep'));
    setupThemeToggle($('#btnTheme'));

    // PnL 着色
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      if(s >= 0) el.classList.add('pos'); else el.classList.add('neg');
    });

    // リサイズで再描画（スパーク/リング）
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t = setTimeout(()=>{
        renderSpark($('#assetSpark'));
        [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
      }, 100);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();