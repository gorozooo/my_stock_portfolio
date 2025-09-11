(function(){
  const $ = (sel, root=document)=>root.querySelector(sel);
  const $$ = (sel, root=document)=>[...root.querySelectorAll(sel)];

  // ===== ユーティリティ =====
  const fmtJPY = (v)=>"¥"+Math.round(v).toLocaleString("ja-JP");
  const clamp = (v, a, b)=>Math.max(a, Math.min(b, v));
  const todayISO = ()=>new Date().toISOString().slice(0,10);

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
    const cash = parseFloat($('#cashBalance')?.dataset.value||"0");
    const total = parseFloat($('#totalAssets')?.dataset.value||"0");
    const margin = parseFloat($('#marginMV')?.dataset.value||"0");
    const cashPct = total ? cash/total : 0;
    const marginPct = total ? margin/total : 0;
    const score = clamp(50*(1-cashPct) + 50*(marginPct), 0, 100);
    el.style.background = `linear-gradient(90deg,
      #26d07c66 ${clamp(100-score,0,100)}%, 
      #ffd16666 ${clamp(100-score+10,0,100)}%, 
      #ff4d6766 ${clamp(100-score+20,0,100)}%)`;
  }

  // ===== ミニ予測（ダミー） =====
  function renderPreview(listEl){
    const ideas = [
      {k:"現金比率", v: ()=> {
        const c = parseFloat($('#cashBalance')?.dataset.value||"0");
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

  // ===== LIVE演出（時刻更新＋点滅） =====
  function setupLive(){
    const ts = $('#liveTs');
    const dot = $('.chip-live .live-dot');
    if(!ts) return;
    function pad(n){ return String(n).padStart(2,'0'); }
    function tick(){
      const d = new Date();
      ts.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      dot && dot.classList.toggle('pulse');
    }
    tick();
    setInterval(tick, 1000);
  }

  // ===== 目標比率乖離／勝率／最大DD推定／配当見込み =====
  function renderAIInsights(){
    const box = $('#aiInsights');
    if(!box) return;

    // 現状比率
    const spot = parseFloat($('#spotMV')?.dataset.value||"0");
    const margin = parseFloat($('#marginMV')?.dataset.value||"0");
    const cash = parseFloat($('#cashBalance')?.dataset.value||"0");
    const total = Math.max(1, parseFloat($('#totalAssets')?.dataset.value||"0"));

    const cur = { spot: spot/total, margin: margin/total, cash: cash/total };

    // 目標: section属性で調整可（デフォ 60/20/20）
    const grid = $('#kpiGrid');
    const tgt = {
      spot:   parseFloat(grid?.dataset.targetSpot||"0.60"),
      margin: parseFloat(grid?.dataset.targetMargin||"0.20"),
      cash:   parseFloat(grid?.dataset.targetCash||"0.20"),
    };
    const dev = {
      spot:   Math.round((cur.spot - tgt.spot) * 100),
      margin: Math.round((cur.margin - tgt.margin) * 100),
      cash:   Math.round((cur.cash - tgt.cash) * 100),
    };

    // 勝率（アクティビティの trade pnl>0 割合）
    const trades = $$('#activityList .act-item[data-kind="trade"]');
    let win=0, lose=0;
    trades.forEach(li=>{
      const v = parseFloat($('[data-amount]', li)?.dataset.amount||"0");
      if(v > 0) win++; else if(v < 0) lose++;
    });
    const wr = (win+lose) ? Math.round(100*win/(win+lose)) : null;

    // 最大ドローダウン（asset_history_csv を使用）
    function maxDrawdown(vals){
      let peak = vals[0], mdd = 0;
      for(let i=1;i<vals.length;i++){
        peak = Math.max(peak, vals[i]);
        mdd = Math.min(mdd, (vals[i]-peak)/peak);
      }
      return Math.round(mdd * 100); // 負の％
    }
    let mddPct = null;
    const raw = ($('#assetSpark')?.dataset.points||'').trim();
    if(raw){
      const vals = raw.split(',').map(Number).filter(v=>!Number.isNaN(v) && v>0);
      if(vals.length >= 3) mddPct = maxDrawdown(vals);
    }

    // 配当着地（最近の配当合計を年率換算っぽく推定）
    const divs = $$('#activityList .act-item[data-kind="dividend"] .act-val');
    let divSum=0;
    divs.forEach(el=>{ divSum += parseFloat(el.dataset.amount||"0"); });
    // ざっくり：最近表示分（最大100件）の平均月額×12
    const monthsApprox = Math.max(1, Math.min(12, Math.ceil(divs.length/3))); // だいたい3件で1ヶ月想定
    const divAnnualEst = Math.round((divSum/monthsApprox)*12);

    // 表示
    const posneg = (n)=> n>=0 ? 'ai-pos' : 'ai-neg';
    box.innerHTML = `
      <li class="ai-item">
        <div><strong>目標比率乖離</strong><br><small>Spot/Margin/Cash vs Target</small></div>
        <div style="text-align:right">
          <div class="${posneg(dev.spot)}">現物 ${dev.spot>=0?'+':''}${dev.spot}%</div>
          <div class="${posneg(dev.margin)}">信用 ${dev.margin>=0?'+':''}${dev.margin}%</div>
          <div class="${posneg(dev.cash)}">現金 ${dev.cash>=0?'+':''}${dev.cash}%</div>
        </div>
      </li>
      <li class="ai-item">
        <div><strong>勝率</strong><br><small>最近の売買履歴ベース</small></div>
        <div><strong>${wr!==null ? wr+'%' : '—'}</strong></div>
      </li>
      <li class="ai-item">
        <div><strong>最大ドローダウン（推定）</strong><br><small>簡易：総資産スパークから</small></div>
        <div><strong>${mddPct!==null ? mddPct+'%' : '—'}</strong></div>
      </li>
      <li class="ai-item">
        <div><strong>配当着地見込み</strong><br><small>最近の配当合計から年率換算（目安）</small></div>
        <div><strong>${fmtJPY(divAnnualEst)}</strong></div>
      </li>
    `;
  }

  // ===== イベント（内訳トグル） =====
  function setupReveal(btn, grid, deep){
    btn?.addEventListener('click', ()=>{
      const show = grid.hasAttribute('hidden');
      if(show){
        grid.removeAttribute('hidden');
        deep.style.display='block';
        btn.textContent='内訳を隠す';
        renderAIInsights(); // 開いたときに算出
      }else{
        grid.setAttribute('hidden','');
        deep.style.display='none';
        btn.textContent='内訳を展開';
      }
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
    setupLive();

    // PnL 着色
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      if(s >= 0) el.classList.add('pos'); else el.classList.add('neg');
    });

    // 日次プレビュー（簡易）
    (function renderPreview(listEl){
      if(!listEl) return;
      const c = parseFloat($('#cashBalance')?.dataset.value||"0");
      const t = parseFloat($('#totalAssets')?.dataset.value||"0") || 1;
      const m = parseFloat($('#marginMV')?.dataset.value||"0");
      const items = [
        {k:"現金比率", v: Math.round(100*c/t) + "%"},
        {k:"信用依存", v: Math.round(100*m/t) + "%"},
        {k:"ヒント", v: (m/t)>0.35 ? "信用比率がやや高め" : "バランス良好"},
      ];
      listEl.innerHTML = items.map(i=>(
        `<div class="mini-item"><span>${i.k}</span><strong>${i.v}</strong></div>`
      )).join("");
    })($('#miniPreview'));

    // リサイズで再描画（スパーク/リング）
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t = setTimeout(()=>{
        renderSpark($('#assetSpark'));
        [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
      }, 120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();