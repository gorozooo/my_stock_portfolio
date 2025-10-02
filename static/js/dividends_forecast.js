(() => {
  const $  = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>Array.from(r.querySelectorAll(s));

  const API = "/dividends/forecast.json";
  const yen = v => "¥" + Math.round(+v || 0).toLocaleString();

  // —— ここが重要：チャートの単一インスタンス管理 ——
  let CHART = null;

  function buildDatasets(payload){
    // stack なし: 合計のみ / stack あり: stacks をそのまま datasets に
    if (payload && payload.stacks){
      // 薄い配色は Chart.js 任せ（スタイルはデフォルト）
      return Object.entries(payload.stacks).map(([label, arr]) => ({
        type: 'bar',
        label,
        data: arr,
        borderWidth: 1,
        fill: true
      }));
    } else {
      const data = (payload.months || []).map(m => m.net || 0);
      return [{
        type: 'bar',
        label: '合計（税後）',
        data,
        borderWidth: 2,
        fill: true
      }];
    }
  }

  function renderChart(payload){
    const el = $("#fChart");
    if (!el) return;

    // 既存チャートを必ず破棄（これをやらないと高さが伸び続ける）
    if (CHART) { CHART.destroy(); CHART = null; }

    const labels = (payload.months || []).map(m => (m.yyyymm || "").slice(5)); // "MM"
    const datasets = buildDatasets(payload);

    // 新規作成（親の .chart-box 高さにフィット）
    CHART = new Chart(el.getContext("2d"), {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,    // 親要素の高さにフィット
        animation: false,
        interaction: { intersect:false, mode:'index' },
        plugins: {
          legend: { display: true, position: 'bottom' },
          tooltip: {
            callbacks: {
              label: ctx => {
                const v = ctx.parsed.y ?? ctx.raw ?? 0;
                return `${ctx.dataset.label}: ${yen(v)}`;
              }
            }
          }
        },
        scales: {
          y: {
            ticks: { callback: v => yen(v) },
            grid: { display: true }
          },
          x: { grid: { display: false } }
        }
      }
    });
  }

  // —— 取得 & 描画 ——
  function qNow(){
    const y = $("#fYear")?.value || new Date().getFullYear();
    const stack = ($("#fStack")?.value || "none").toLowerCase(); // none|broker|account
    return { year: y, stack };
  }

  function fetchAndRender(q){
    const p = new URLSearchParams(q);
    fetch(`${API}?${p.toString()}`, { credentials: "same-origin" })
      .then(r => r.json())
      .then(json => renderChart(json))
      .catch(() => {/* no-op */});
  }

  // フィルター変更で再取得（イベント多重登録を避ける）
  const filter = $("#forecastFilter");
  if (filter){
    ["change","input"].forEach(ev=>{
      filter.addEventListener(ev, () => fetchAndRender(qNow()), { passive:true });
    });
  }

  // ウィンドウリサイズで再計算だけ（destroy/new不要）
  // Chart.js は responsive:true でリサイズに追従するので resize() 呼び出しでOK
  window.addEventListener("resize", debounce(() => {
    if (CHART) CHART.resize();
  }, 120), { passive:true });

  function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

  // 初期描画（サーバー埋め込みがあればそれを使い、なければ取得）
  const init = window.__FORECAST_INIT__;
  if (init && init.months) renderChart(init);
  else fetchAndRender(qNow());
})();