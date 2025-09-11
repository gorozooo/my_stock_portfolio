(function(){
  const $ = (sel, root=document)=>root.querySelector(sel);
  const $$ = (sel, root=document)=>[...root.querySelectorAll(sel)];
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY = (v)=>"¥"+Math.round(v).toLocaleString("ja-JP");

  // 数値アニメ
  function animateNumber(el, to, dur=700){
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduce){ el.textContent = fmtJPY(to); return; }
    const from = parseFloat(el.dataset.value||to) || 0;
    const start = performance.now();
    function tick(now){
      const t = clamp((now-start)/dur, 0, 1);
      const val = from + (to-from)*(1-Math.pow(1-t,3));
      el.textContent = fmtJPY(val);
      if(t<1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
    el.dataset.value = to;
  }

  // スパーク
  function renderSpark(el){
    if(!el) return;
    const raw = (el.dataset.points||"").trim();
    const vals = raw.split(",").map(Number).filter(v=>!Number.isNaN(v));
    if(vals.length<2){ el.textContent="データなし"; return; }
    const w = el.clientWidth||320, h = el.clientHeight||60, pad=6;
    const min = Math.min(...vals), max = Math.max(...vals);
    const x = i => pad + (w-pad*2)*(i/(vals.length-1));
    const y = v => max===min ? h/2 : pad + (1-((v-min)/(max-min)))*(h-pad*2);
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

  // 円リング
  function renderRing(el){
    if(!el) return;
    const val = parseFloat(el.dataset.value||"0");
    const total = Math.max(1, parseFloat(el.dataset.total||"1"));
    const pct = clamp(val/total, 0, 1);
    const size = el.clientHeight || 86;
    const r = (size/2)-8, c = Math.PI*2*r;
    const dash = c*pct, gap = c-dash;
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

  // スタックバー
  function renderStackBars(el){
    if(!el) return;
    const spot = parseFloat(el.dataset.spot||"0");
    const margin = parseFloat(el.dataset.margin||"0");
    const cash = parseFloat(el.dataset.cash||"0");
    const total = Math.max(1, spot+margin+cash);
    const p = v => Math.max(2, (v/total)*100);
    el.innerHTML = `
      <span style="width:${p(spot)}%;background:var(--primary)"></span>
      <span style="width:${p(margin)}%;background:#ff8a5b"></span>
      <span style="width:${p(cash)}%;background:var(--accent)"></span>`;
  }

  // リスク
  function renderRisk(el){
    if(!el) return;
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

  // ミニプレビュー
  function renderPreview(listEl){
    if(!listEl) return;
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

  // 開閉（リンク風）
  function setupDisclosure(){
    const btn = $('#discloseKPI');
    const grid = $('#kpiGrid');
    const deep = $('#deep');
    if(!btn || !grid) return;

    btn.addEventListener('click', ()=>{
      const open = btn.getAttribute('aria-expanded') === 'true';
      if(open){
        grid.setAttribute('hidden','');
        deep && deep.setAttribute('hidden','');
        btn.setAttribute('aria-expanded','false');
        btn.innerHTML = `内訳を表示
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>`;
      }else{
        grid.removeAttribute('hidden');
        deep && deep.removeAttribute('hidden');
        btn.setAttribute('aria-expanded','true');
        btn.innerHTML = `内訳を隠す
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>`;
        grid.classList.add('revealed');
        setTimeout(()=>grid.classList.remove('revealed'), 400);
      }
    });

    $('#btnCollapse')?.addEventListener('click', ()=>{
      grid.setAttribute('hidden','');
      deep && deep.setAttribute('hidden','');
      btn.setAttribute('aria-expanded','false');
      btn.innerHTML = `内訳を表示
        <svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>`;
    });
  }

  function init(){
    const totalEl = $('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    renderSpark($('#assetSpark'));
    [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
    renderStackBars($('#stackBars'));
    renderRisk($('#riskHeat'));
    renderPreview($('#miniPreview'));

    const deck = $('#sparkDeck');
    if(deck){
      const raw = (deck.dataset.points||'').trim();
      deck.innerHTML = `<div class="spark" data-points="${raw}"></div><div class="spark" data-points="${raw}"></div>`;
      $$('.spark', deck).forEach(renderSpark);
    }

    setupDisclosure();

    // PnL 色
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      if(s>=0) el.classList.add('pos'); else el.classList.add('neg');
    });

    // リサイズ再描画
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t=setTimeout(()=>{
        renderSpark($('#assetSpark'));
        [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
      },120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();