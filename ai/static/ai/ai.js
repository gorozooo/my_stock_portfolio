(() => {
  const items = window.__AI_ITEMS__ || [];
  const modal = document.getElementById('aiModal');
  const closeBtns = modal.querySelectorAll('[data-close]');
  const nameEl = document.getElementById('m_name');
  const starsEl = document.getElementById('m_stars');
  const scoreEl = document.getElementById('m_score');
  const sectorEl = document.getElementById('m_sector');
  const tdEl = document.getElementById('m_trend_d');
  const twEl = document.getElementById('m_trend_w');
  const tmEl = document.getElementById('m_trend_m');
  const reasonsEl = document.getElementById('m_reasons');
  const pricesEl = document.getElementById('m_prices');
  const qtyEl = document.getElementById('m_qty');
  const canvas = document.getElementById('m_chart');

  const icon = (d) => d === 'up' ? '⤴️' : (d === 'down' ? '⤵️' : '➡️');

  // カード → 詳細開く
  document.querySelectorAll('.ai-card .more').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = Number(btn.dataset.open || 0);
      const it = items[idx];
      if (!it) return;

      // ヘッダー
      nameEl.textContent = `${it.name} (${it.code})`;
      sectorEl.textContent = it.sector;
      scoreEl.textContent = `${it.score}点`;
      starsEl.textContent = '⭐️'.repeat(it.stars) + '☆'.repeat(5 - it.stars);
      tdEl.textContent = `日足 ${icon(it.trend.d)}`;
      twEl.textContent = `週足 ${icon(it.trend.w)}`;
      tmEl.textContent = `月足 ${icon(it.trend.m)}`;

      // 理由
      reasonsEl.innerHTML = '';
      (it.reasons || []).forEach(r => {
        const li = document.createElement('li'); li.textContent = r; reasonsEl.appendChild(li);
      });

      // 価格・数量
      pricesEl.innerHTML = `エントリー目安：<b>${it.prices.entry}</b> ／ 利確：<b>${it.prices.tp}</b> ／ 損切：<b>${it.prices.sl}</b>`;
      qtyEl.innerHTML = `提案数量：<b>${it.qty.shares}株</b>（必要資金 <b>${it.qty.capital.toLocaleString()}</b>円、想定利益 <b>${it.qty.pl_plus.toLocaleString()}</b>円、想定損失 <b>${it.qty.pl_minus.toLocaleString()}</b>円、R=<b>${it.qty.r}</b>）`;

      // チャート（ダミーの小系列＋水平ライン3本）
      drawChart(canvas, it);

      modal.classList.remove('hidden');
      modal.setAttribute('aria-hidden', 'false');
    });
  });

  // 閉じる
  closeBtns.forEach(b => b.addEventListener('click', close));
  modal.querySelector('.backdrop').addEventListener('click', close);
  function close() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
  }

  // ---- Chart.js ----
  let _chart = null;
  function drawChart(cv, it) {
    const ctx = cv.getContext('2d');
    if (_chart) { _chart.destroy(); _chart = null; }

    // 軽量ダミーデータ（将来は銘柄の短期履歴を注入）
    const base = it.prices.entry;
    const series = Array.from({length: 40}, (_,i) => (base * (0.98 + 0.04 * Math.random())));
    const labels = Array.from({length: series.length}, (_,i) => `${i}`);

    _chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Price', data: series, tension: 0.25, pointRadius: 0 },
          { label: 'Entry', data: series.map(_ => it.prices.entry), borderDash: [6,4], pointRadius: 0 },
          { label: 'TP',    data: series.map(_ => it.prices.tp),    borderDash: [2,4], pointRadius: 0 },
          { label: 'SL',    data: series.map(_ => it.prices.sl),    borderDash: [2,4], pointRadius: 0 },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true } },
        scales: { x: { display: false } }
      }
    });
  }
})();