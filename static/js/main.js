// main.js – lightweight, no deps
document.addEventListener('DOMContentLoaded', () => {
  initRing();
  initSparkline();
  initGauges();
  initActivityFilters();
});

/* ===== Hero ring progress ===== */
function initRing() {
  const ring = document.querySelector('.ring');
  if (!ring) return;

  const svg = ring.querySelector('svg');
  const fg  = svg.querySelector('.fg');
  const r   = 50; // viewBox半径
  const C   = 2 * Math.PI * r;

  fg.style.strokeDasharray = `${C} ${C}`;

  const val = toNumber(ring.dataset.value);
  const maxRaw = toNumber(ring.dataset.max);
  // ターゲット未提供なら「現在値を最大＝100%」で表示
  const max = maxRaw > 0 ? maxRaw : (val > 0 ? val : 1);

  const pct = Math.max(0, Math.min(1, val / max));
  const targetOffset = C * (1 - pct);

  // アニメーション
  let t = 0;
  const duration = 800;
  const start = performance.now();
  function tick(now) {
    t = Math.min(1, (now - start) / duration);
    const eased = easeOutCubic(t);
    const currentOffset = C * (1 - eased * pct);
    fg.style.strokeDashoffset = currentOffset;
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function easeOutCubic(x){ return 1 - Math.pow(1 - x, 3); }
function toNumber(x){ const n = Number(String(x||'').replace(/[^\d.-]/g,'')); return isFinite(n) ? n : 0; }

/* ===== Sparkline (inline SVG) ===== */
function initSparkline() {
  const el = document.getElementById('assetSpark');
  if (!el) return;

  const csv = (el.dataset.points || '').trim();
  if (!csv) {
    el.innerHTML = '<svg viewBox="0 0 300 56"></svg>';
    return;
  }

  const nums = csv.split(/[,\s]+/).map(toNumber).filter(n => !isNaN(n));
  if (nums.length < 2) {
    el.innerHTML = '<svg viewBox="0 0 300 56"></svg>';
    return;
  }

  const w = el.clientWidth || 300;
  const h = el.clientHeight || 56;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = Math.max(1, max - min);

  const pts = nums.map((v,i) => {
    const x = (i/(nums.length-1)) * (w-8) + 4; // 4px padding
    const y = h - 4 - ((v - min) / span) * (h-8);
    return [x,y];
  });

  const path = pts.map((p,i) => (i===0?`M${p[0]},${p[1]}`:`L${p[0]},${p[1]}`)).join(' ');
  // fill 下部グラデ
  const fill = `${path} L${pts[pts.length-1][0]},${h-2} L${pts[0][0]},${h-2} Z`;

  const svg = `
  <svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="資産推移スパークライン">
    <defs>
      <linearGradient id="grad" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="rgba(59,130,246,0.55)"/>
        <stop offset="100%" stop-color="rgba(59,130,246,0.05)"/>
      </linearGradient>
    </defs>
    <path d="${fill}" fill="url(#grad)"/>
    <path d="${path}" fill="none" stroke="rgba(59,130,246,1)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
  </svg>`;
  el.innerHTML = svg;
}

/* ===== Gauges ===== */
function initGauges() {
  const gauges = document.querySelectorAll('.gauge');
  if (!gauges.length) return;

  // スケールは簡易的に：0円〜最大の絶対値で正負カラー
  let maxAbs = 1;
  gauges.forEach(g => { maxAbs = Math.max(maxAbs, Math.abs(toNumber(g.dataset.val))); });

  gauges.forEach(g => {
    const v = toNumber(g.dataset.val);
    const bar = g.querySelector('.g-bar span');
    const ratio = Math.min(1, Math.abs(v)/maxAbs);
    const target = Math.round(ratio*100);

    // 色味（正負でグラデを変える）
    bar.style.background = v >= 0
      ? 'linear-gradient(90deg, rgba(52,211,153,.6), rgba(52,211,153,1))'
      : 'linear-gradient(90deg, rgba(248,113,113,.6), rgba(248,113,113,1))';
    bar.style.boxShadow = v >= 0
      ? '0 0 10px rgba(52,211,153,.6)'
      : '0 0 10px rgba(248,113,113,.6)';

    animateWidth(bar, target, 700);
  });
}

function animateWidth(el, targetPercent, dur=800){
  let t=0; const start=performance.now();
  function tick(now){
    t = Math.min(1,(now-start)/dur);
    const eased = easeOutCubic(t);
    el.style.width = (eased*targetPercent)+'%';
    if(t<1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/* ===== Activity filter chips -> クエリ切替 ===== */
function initActivityFilters(){
  const chips = document.querySelectorAll('.activity .chips .chip');
  if (!chips.length) return;
  const current = new URL(window.location.href);

  chips.forEach(ch => {
    const r = ch.dataset.range;
    if (!r) return;
    // 現在のrangeに合わせてactiveを付与（SSRで付いてる場合もあるが保険）
    if ((current.searchParams.get('range') || '7') === r) {
      chips.forEach(c=>c.classList.remove('active'));
      ch.classList.add('active');
    }
    ch.addEventListener('click', () => {
      current.searchParams.set('range', r);
      // 他のフィルタを維持したい場合はここで保持、不要なら消去
      window.location.href = current.toString();
    });
  });
}