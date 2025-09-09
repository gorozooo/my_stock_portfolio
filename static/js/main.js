// ===== Tabs (broker) =====
document.addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;

  const key = tab.dataset.tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));

  tab.classList.add('active');
  const pane = document.getElementById(`pane-${key}`);
  if (pane) pane.classList.add('active');
});

// ===== Range chips (recent activities) =====
document.addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  const range = chip.dataset.range;
  const url = new URL(window.location.href);
  if (range === 'all') {
    url.searchParams.set('range', 'all');
  } else {
    url.searchParams.set('range', range);
  }
  window.location.href = url.toString();
});

// ===== Sparkline (asset history) =====
(function renderSpark() {
  const el = document.getElementById('assetSpark');
  if (!el) return;
  const raw = (el.getAttribute('data-points') || '').trim();
  if (!raw) { el.textContent = 'データなし'; return; }

  const vals = raw.split(',').map(s => parseFloat(s)).filter(v => !Number.isNaN(v));
  if (vals.length < 2) { el.textContent = 'データ不足'; return; }

  const w = el.clientWidth || 320;
  const h = el.clientHeight || 60;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const pad = 4;

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
  const r = 52;
  const C = 2 * Math.PI * r;

  const target = parseFloat(svg.dataset.target || '0');
  const value  = parseFloat(svg.dataset.value  || '0');

  const fg = svg.querySelector('.fg');
  if (!fg) return;

  let ratio = 0;
  if (target > 0) ratio = Math.max(0, Math.min(1, value / target));
  if (target <= 0) ratio = 0.6; // 目標未設定時の演出値

  const len = C * ratio;
  fg.setAttribute('stroke-dasharray', `${len} ${C - len}`);
  fg.setAttribute('stroke-dashoffset', '0');
})();

// ===== Gauges (bar animation + width calculation in JS) =====
(function animateBars() {
  // 1) 単純な比率バー（評価/総資産・現金/総資産）
  document.querySelectorAll('.g-bar span[data-ratio]').forEach(span => {
    const num = parseFloat(span.getAttribute('data-num') || '0');
    const den = parseFloat(span.getAttribute('data-den') || '0');
    let pct = 0;
    if (den > 0 && num >= 0) {
      pct = Math.max(0, Math.min(100, (num / den) * 100));
    }
    span.style.width = '0';
    requestAnimationFrame(() => {
      span.style.transition = 'width .9s cubic-bezier(.2,.8,.2,1)';
      setTimeout(() => { span.style.width = pct + '%'; }, 10);
    });
  });

  // 2) 含み益率バー： mvが分母、u/mv を -∞..+∞ → 0..100 に写像（0%= -100%以下, 50%= ±0%, 100%= +100%以上）
  document.querySelectorAll('.g-bar span[data-profit]').forEach(span => {
    const u  = parseFloat(span.getAttribute('data-u')  || '0');
    const mv = parseFloat(span.getAttribute('data-mv') || '0');
    let pct = 0;
    if (mv > 0) {
      const ratio = u / mv;             // -1.0 = -100%, 0 = 0%, 1.0 = +100%
      pct = (ratio * 50) + 50;          // 中心50%にマップ
      pct = Math.max(0, Math.min(100, pct));
    }
    span.style.width = '0';
    requestAnimationFrame(() => {
      span.style.transition = 'width .9s cubic-bezier(.2,.8,.2,1)';
      setTimeout(() => { span.style.width = pct + '%'; }, 10);
    });
  });
})();