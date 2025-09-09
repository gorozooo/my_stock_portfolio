/* static/js/main_home.js */
/* =========================================================
   Home Dashboard Interactions (Full)
   - Broker Tabs (with persistence)
   - Range Chips (query param update)
   - Sparkline (responsive SVG)
   - Ring Gauge (total vs target)
   - Gauges (animated bars)
   ========================================================= */

/* ---------------- Tabs (brokers) ---------------- */
(function tabsModule() {
  const TAB_KEY = 'home.activeBroker';
  const tabs = document.querySelectorAll('.tabs .tab');
  const panes = document.querySelectorAll('.panes .pane');

  function activate(key) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === key));
    panes.forEach(p => p.classList.toggle('active', p.id === `pane-${key}`));
    try { localStorage.setItem(TAB_KEY, key); } catch (_) {}
  }

  // 初期化（保存されたキーが有効なら採用）
  let initial = null;
  try { initial = localStorage.getItem(TAB_KEY); } catch (_) {}
  if (initial) {
    const has = Array.from(tabs).some(t => t.dataset.tab === initial);
    if (has) activate(initial);
  }

  document.addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (!tab || !tab.dataset.tab) return;
    activate(tab.dataset.tab);
  });
})();

/* ---------------- Range chips (recent activities) ---------------- */
(function rangeChips() {
  document.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    const range = chip.dataset.range;
    const url = new URL(window.location.href);
    if (range) url.searchParams.set('range', range);
    window.location.href = url.toString();
  });
})();

// ===== Sparkline (asset history) =====
(function renderSpark() {
  const el = document.getElementById('assetSpark');
  if (!el) return;
  const raw = (el.getAttribute('data-points') || '').trim();
  if (!raw) { el.style.display = 'none'; return; }

  const vals = raw.split(',').map(s => parseFloat(s)).filter(v => !Number.isNaN(v));
  if (vals.length < 2) { el.style.display = 'none'; return; }

  const w = el.clientWidth || 320;
  const h = el.clientHeight || 84;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const pad = 6;

  const scaleX = (i) => pad + (w - pad * 2) * (i / (vals.length - 1));
  const scaleY = (v) => {
    if (max === min) return h / 2;
    const t = (v - min) / (max - min);
    return pad + (1 - t) * (h - pad * 2);
  };

  const pts = vals.map((v, i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const area = ['0,' + h, pts, w + ',' + h].join(' ');
  el.innerHTML = `
    <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <polyline points="${pts}" fill="none" stroke="rgba(96,165,250,1)" stroke-width="2" />
      <polyline points="${pts}" fill="none" stroke="rgba(96,165,250,.35)" stroke-width="6" opacity=".35" />
      <polyline points="${area}" fill="rgba(96,165,250,.18)" />
    </svg>
  `;
})();

/* ---------------- Ring Gauge (total assets vs target) ---------------- */
(function ringGaugeModule() {
  // 親 .ring に data-total/data-target がある想定（旧JS互換で svg 側の data- も読む）
  const ring = document.querySelector('.ring');
  if (!ring) return;

  const svg = ring.querySelector('.ring-svg');
  const fg = ring.querySelector('.fg');
  if (!svg || !fg) return;

  // データは親→SVG の優先順で取得
  function num(attrOnRing, attrOnSvg, fallback = 0) {
    const a = ring.getAttribute(attrOnRing) ?? ring.dataset[attrOnRing?.replace(/^data-/, '')];
    const b = svg.getAttribute(attrOnSvg) ?? svg.dataset[attrOnSvg?.replace(/^data-/, '')];
    const v = parseFloat(a ?? b ?? fallback);
    return Number.isFinite(v) ? v : 0;
  }

  const r = 52;
  const C = 2 * Math.PI * r;

  const total = num('data-total', 'data-value', 0);
  const target = num('data-target', 'data-target', 0);

  let ratio = 0.6; // 目標未設定時の演出値
  if (target > 0) ratio = Math.max(0, Math.min(1, total / target));

  const len = C * ratio;
  fg.setAttribute('stroke-dasharray', `${len} ${Math.max(0, C - len)}`);
  fg.setAttribute('stroke-dashoffset', '0');
})();

/* ---------------- Gauges (bar animation + width calculation) ---------------- */
(function gaugeBars() {
  // 比率バー（num/den → 0..100%）
  document.querySelectorAll('.g-bar span[data-ratio]').forEach(span => {
    const num = parseFloat(span.getAttribute('data-num') || '0');
    const den = parseFloat(span.getAttribute('data-den') || '0');
    let pct = 0;
    if (den > 0 && num >= 0) pct = Math.max(0, Math.min(100, (num / den) * 100));
    // アニメーション
    span.style.width = '0';
    requestAnimationFrame(() => {
      span.style.transition = 'width .9s cubic-bezier(.2,.8,.2,1)';
      setTimeout(() => { span.style.width = pct + '%'; }, 10);
    });
  });

  // 含み益率バー（u/mv を -100%以下→0%, 0%→50%, +100%以上→100%）
  document.querySelectorAll('.g-bar span[data-profit]').forEach(span => {
    const u  = parseFloat(span.getAttribute('data-u')  || '0');  // unrealized profit
    const mv = parseFloat(span.getAttribute('data-mv') || '0');  // market value
    let pct = 50;
    if (mv > 0) {
      const ratio = u / mv;       // -1 = -100%, 0 = 0%, 1 = +100%
      pct = (ratio * 50) + 50;    // 0..100
      pct = Math.max(0, Math.min(100, pct));
    }
    span.style.width = '0';
    requestAnimationFrame(() => {
      span.style.transition = 'width .9s cubic-bezier(.2,.8,.2,1)';
      setTimeout(() => { span.style.width = pct + '%'; }, 10);
    });
  });
})();