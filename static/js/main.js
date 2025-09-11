(function(){
  const $ = (sel, root=document)=>root.querySelector(sel);
  const $$ = (sel, root=document)=>[...root.querySelectorAll(sel)];
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const fmtJPY = (v)=>"¥"+Math.round(v).toLocaleString("ja-JP");

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

  function renderRing(el){
    if(!el) return;
    const val = parseFloat(el.dataset.value||"0");
    const total = Math.max(1, parseFloat(el.dataset.total||"1"));
    const pct = Math.max(0, Math.min(1, val/total));
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

  // 内訳の開閉（hero に is-collapsed を付け外しして余白を制御）
  function setupDisclosure(){
    const btn = $('#discloseKPI');
    const panel = $('#heroDisclosure');
    const hero = $('.hero');
    if(!btn || !panel || !hero) return;

    // 初期：hiddenなら is-collapsed を確実に付ける
    if(panel.hasAttribute('hidden')) {
      hero.classList.add('is-collapsed');
    }

    btn.addEventListener('click', ()=>{
      const open = btn.getAttribute('aria-expanded') === 'true';
      if(open){
        panel.setAttribute('hidden','');
        btn.setAttribute('aria-expanded','false');
        btn.innerHTML = `内訳を表示
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>`;
        hero.classList.add('is-collapsed'); // ← 閉じると余白を詰める
      }else{
        panel.removeAttribute('hidden');
        btn.setAttribute('aria-expanded','true');
        btn.innerHTML = `内訳を隠す
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>`;
        hero.classList.remove('is-collapsed'); // ← 開くと通常余白
        // 可視化部品を描画
        [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
        renderStackBars($('#stackBars'));
      }
    });
  }

  function init(){
    const totalEl = $('#totalAssets');
    if(totalEl) animateNumber(totalEl, parseFloat(totalEl.dataset.value||"0"));

    renderSpark($('#assetSpark'));

    // PnL 色
    $$('.pnl').forEach(el=>{
      const s = parseFloat(el.dataset.sign||"0");
      if(s>=0) el.classList.add('pos'); else el.classList.add('neg');
    });

    setupDisclosure();

    // リサイズ再描画
    let t; window.addEventListener('resize', ()=>{
      clearTimeout(t);
      t=setTimeout(()=>{
        renderSpark($('#assetSpark'));
        if($('#heroDisclosure') && !$('#heroDisclosure').hasAttribute('hidden')){
          [$('#ringSpot'), $('#ringMargin')].forEach(el=>el && renderRing(el));
        }
      },120);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();