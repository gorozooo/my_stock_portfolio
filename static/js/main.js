// 軽量スパークライン生成（CSV値を <svg> に描画）
(function () {
  function drawSparkline(el) {
    const dataAttr = el.getAttribute('data-points') || '';
    if (!dataAttr.trim()) return;
    const points = dataAttr.split(',').map(s => parseFloat(s.trim())).filter(v => !isNaN(v));
    if (points.length < 2) return;

    const W = el.clientWidth || el.offsetWidth || 320;
    const H = el.clientHeight || 48;
    const pad = 6;

    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = (max - min) || 1;

    const stepX = (W - pad * 2) / (points.length - 1);
    const toX = (i) => pad + i * stepX;
    const toY = (v) => H - pad - ((v - min) / span) * (H - pad * 2);

    let d = '';
    points.forEach((v, i) => {
      const x = toX(i), y = toY(v);
      d += (i === 0 ? `M${x},${y}` : ` L${x},${y}`);
    });

    const svg =
      `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="総資産推移">
         <path d="${d}" fill="none" stroke="rgba(59,130,246,.9)" stroke-width="2"/>
       </svg>`;
    el.innerHTML = svg;
  }

  const spark = document.getElementById('assetSpark');
  if (spark) {
    const ro = new ResizeObserver(() => drawSparkline(spark));
    ro.observe(spark);
    drawSparkline(spark);
  }
})();

// ブローカータブ切替（クライアントのみ）
(function () {
  const tabs = document.querySelectorAll('.brokers .tab');
  const panes = document.querySelectorAll('.brokers .pane');
  if (!tabs.length) return;
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const key = tab.dataset.tab;
      tabs.forEach(t => t.classList.toggle('active', t === tab));
      panes.forEach(p => p.classList.toggle('active', p.id === `pane-${key}`));
    });
  });
})();

// 最近のアクティビティ：期間フィルタ（クライアントサイド絞込）
(function () {
  const chips = document.querySelectorAll('.activity .chip');
  const list = document.getElementById('timeline');
  if (!chips.length || !list) return;

  // サーバーから来た行を保持
  const rows = Array.from(list.querySelectorAll('.trow'));
  const basePayload = rows.map(li => {
    const dateText = li.querySelector('.tdate')?.textContent || '';
    // "n/j" 形式を Date に（年は今年で近似）
    const [m, d] = dateText.split('/').map(s => parseInt(s, 10));
    const now = new Date();
    const dt = new Date(now.getFullYear(), (m || 1) - 1, d || 1);
    return { el: li, date: dt.getTime() };
  });

  function apply(range) {
    const now = Date.now();
    let from = -Infinity;
    if (range !== 'all') {
      const days = parseInt(range, 10);
      from = now - (days * 24 * 60 * 60 * 1000);
    }
    rows.forEach(r => r.style.display = ''); // reset
    basePayload.forEach(({ el, date }) => {
      if (date < from) el.style.display = 'none';
    });
  }

  chips.forEach(ch => {
    ch.addEventListener('click', () => {
      chips.forEach(c => c.classList.remove('active'));
      ch.classList.add('active');
      apply(ch.dataset.range || 'all');
    });
  });

  // 初期適用
  const act = document.querySelector('.activity .chip.active');
  apply(act?.dataset.range || '7');
})();