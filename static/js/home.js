document.addEventListener("DOMContentLoaded", () => {
  // ---- データ供給 ----
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, cash_bars: [], liquidation: 0, liquidity_rate_pct: 0, stress_base_mv: 0 };
  try { data = JSON.parse(dataEl.textContent); } catch(e){}

  // ---- KPI色分け（+緑 / -赤）----
  document.querySelectorAll('.kpi-pnl').forEach(el=>{
    const val = Number(el.getAttribute('data-sign') || "0");
    if (val > 0) el.classList.add('pos');
    if (val < 0) el.classList.add('neg');
  });

  // ---- キャッシュフロー棒グラフ ----
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = data.cash_bars.map(x=>x.label);
    const values = data.cash_bars.map(x=>x.value);
    // 値が全部0でもグラフは出す（空に見えないように）
    const hasAny = values.some(v=>Number(v) !== 0);
    const plotVals = hasAny ? values : values.map(()=>0.0001);
    new Chart(cashCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "今月", data: plotVals, borderWidth: 1 }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (ctx)=> "¥" + Math.round(ctx.parsed.y).toLocaleString()
        }}},
        scales: { y: { beginAtZero: true, ticks:{ callback:(v)=>"¥"+Number(v).toLocaleString() } } }
      }
    });
  }

  // ---- ストレステスト ----
  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  const baseMV = Number(data.stress_base_mv || 0);
  const beta = 0.9;
  const updateStress = () => {
    const pct = Number(slider.value);
    const mv = Math.round(baseMV * (1 + beta * pct/100));
    pctEl.textContent = pct.toString();
    mvEl.textContent = "¥" + mv.toLocaleString();
  };
  if (slider && mvEl && pctEl) {
    slider.addEventListener("input", updateStress);
    updateStress();
  }

  // ---- AIボタン（プレースホルダ）----
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）。直近の推移・勝率・強弱セクターを要約します。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手レコメンド（AI接続予定）。現金比率・含み益・セクター偏りから提案します。");
  });
});