(function(){
  const elCanvas = document.getElementById('monthlyChart');
  if (!elCanvas) return;

  let chart;

  async function fetchData() {
    // 検索キーワードを付与（id="q" 前提）
    const q = (document.getElementById('q')?.value || '').trim();
    const url = `/realized/chart-monthly.json${q ? ('?q=' + encodeURIComponent(q)) : ''}`;
    const res = await fetch(url, {headers: {'HX-Request': 'true'}});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json(); // {labels, pnl, cash}
  }

  function buildChart(data) {
    const {labels, pnl, cash} = data;

    // すでに描画済みなら破棄
    if (chart) { chart.destroy(); chart = null; }

    // ダークUIに合わせた共通オプション
    const axisGrid = { color: 'rgba(255,255,255,.08)' };
    const axisTicks = { color: 'rgba(226,232,240,.8)', callback: v => '¥' + Number(v).toLocaleString() };

    chart = new Chart(elCanvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          // PnL：棒（プラ緑・マイナス赤で自動着色）
          {
            type: 'bar',
            label: 'PnL（投資家損益）',
            data: pnl,
            yAxisID: 'yPnL',
            borderWidth: 0,
            borderRadius: 8,
            backgroundColor: ctx => {
              const v = ctx.raw || 0;
              return v >= 0 ? 'rgba(16,185,129,.8)' : 'rgba(244,63,94,.8)';
            },
            hoverBackgroundColor: ctx => {
              const v = ctx.raw || 0;
              return v >= 0 ? 'rgba(16,185,129,1)' : 'rgba(244,63,94,1)';
            },
          },
          // 現金フロー：折れ線（第2軸）
          {
            type: 'line',
            label: '現金フロー',
            data: cash,
            yAxisID: 'yCash',
            tension: 0.35,
            pointRadius: 3,
            pointHoverRadius: 5,
            borderWidth: 2,
            // 色は固定（情報の主役はPnLなので控えめ）
            borderColor: 'rgba(99,102,241,1)',
            backgroundColor: 'rgba(99,102,241,.25)',
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            labels: { color: 'rgba(226,232,240,.9)' }
          },
          tooltip: {
            callbacks: {
              label(ctx) {
                const v = Number(ctx.raw || 0);
                return `${ctx.dataset.label}: ¥${v.toLocaleString()}`;
              }
            }
          }
        },
        scales: {
          x: {
            ticks: { color: 'rgba(226,232,240,.8)' },
            grid: { display: false }
          },
          yPnL: {
            position: 'left',
            grid: axisGrid,
            ticks: axisTicks
          },
          yCash: {
            position: 'right',
            grid: { display: false },
            ticks: axisTicks
          }
        }
      }
    });
  }

  async function render() {
    try {
      const data = await fetchData();
      await buildChart(data);
    } catch (e) {
      console.error('monthly chart error:', e);
    }
  }

  // 表示時
  render();

  // トグル（表示/非表示）
  const togglePnl  = document.getElementById('togglePnl');
  const toggleCash = document.getElementById('toggleCash');
  function applyToggles(){
    if (!chart) return;
    // datasets[0]=PnL, [1]=Cash の前提
    chart.getDatasetMeta(0).hidden = togglePnl && !togglePnl.checked;
    chart.getDatasetMeta(1).hidden = toggleCash && !toggleCash.checked;
    chart.update();
  }
  togglePnl?.addEventListener('change', applyToggles);
  toggleCash?.addEventListener('change', applyToggles);

  // 検索やHTMXの差し替え後に再描画
  document.getElementById('q')?.addEventListener('input', () => {
    // 打鍵から少し待ってから
    clearTimeout(window.__monthlyChartTimer);
    window.__monthlyChartTimer = setTimeout(render, 300);
  });
  document.body.addEventListener('htmx:afterSwap', (e) => {
    // サマリー/テーブルが入れ替わった後にも再描画
    if (e.target && (e.target.id === 'pnlSummaryWrap' || e.target.id === 'pnlTableWrap')) {
      render();
    }
  });
})();