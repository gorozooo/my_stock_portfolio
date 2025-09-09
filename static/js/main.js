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

// ===== Ring Gauge (total assets vs target) =====
(function ringGauge() {
  const svg = document.querySelector('.ring-svg');
  if (!svg) return;
  const r = 52, C = 2 * Math.PI * r;
  const target = parseFloat(svg.dataset.target || '0');
  const value  = parseFloat(svg.dataset.value  || '0');
  const fg = svg.querySelector('.fg'); if (!fg) return;

  let ratio = 0;
  if (target > 0) ratio = Math.max(0, Math.min(1, value / target));
  if (target <= 0) ratio = 0.6; // 目標未設定時の見栄え
  const len = C * ratio;
  fg.setAttribute('stroke-dasharray', `${len} ${C - len}`);
  fg.setAttribute('stroke-dashoffset', '0');
})();

// ===== Bars (ratio against total assets) =====
(function animateBars() {
  document.querySelectorAll('.g-bar span[data-ratio]').forEach(span => {
    const num = parseFloat(span.getAttribute('data-num') || '0');
    const den = parseFloat(span.getAttribute('data-den') || '0');
    let pct = 0;
    if (den > 0 && num >= 0) pct = Math.max(0, Math.min(100, (num / den) * 100));
    span.style.width = '0';
    requestAnimationFrame(() => {
      span.style.transition = 'width .9s cubic-bezier(.2,.8,.2,1)';
      setTimeout(() => { span.style.width = pct + '%'; }, 10);
    });
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

  const sx = (i) => pad + (w - pad * 2) * (i / (vals.length - 1));
  const sy = (v) => max === min ? h / 2 : pad + (1 - ((v - min) / (max - min))) * (h - pad * 2);

  const pts = vals.map((v, i) => `${sx(i)},${sy(v)}`).join(' ');
  const area = ['0,' + h, pts, w + ',' + h].join(' ');
  el.innerHTML = `
    <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <polyline points="${pts}" fill="none" stroke="rgba(96,165,250,1)" stroke-width="2" />
      <polyline points="${pts}" fill="none" stroke="rgba(96,165,250,.35)" stroke-width="6" opacity=".35" />
      <polyline points="${area}" fill="rgba(96,165,250,.18)" />
    </svg>
  `;
})();