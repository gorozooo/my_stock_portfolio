document.addEventListener("DOMContentLoaded", () => {
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, sectors: [], cash_bars: [] };
  try { data = JSON.parse(dataEl.textContent); } catch(e){}

  // ストレステスト
  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  const totalMV = data.total_mv || 0;
  const beta = 0.9;

  const updateStress = () => {
    const pct = Number(slider.value);
    const mv = Math.round(totalMV * (1 + beta * pct/100));
    pctEl.textContent = pct.toString();
    mvEl.textContent = "¥" + mv.toLocaleString();
  };
  slider?.addEventListener("input", updateStress);
  updateStress();

  // キャッシュフロー棒グラフ
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = (data.cash_bars || []).map(x=>x.label);
    const values = (data.cash_bars || []).map(x=>x.value);
    new Chart(cashCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "今月", data: values, borderWidth: 1 }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  // AIボタン（将来：GPT接続）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）。直近の推移・勝率・強弱セクターを要約します。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手レコメンド（AI接続予定）。現金比率・含み益・セクター偏りから提案します。");
  });

  // KPIの色（プラス=緑 / マイナス=赤）※サーバ側で付与済みだが保険で実装
  (function(){
    const toNum = (s)=> Number(String(s).replace(/[^0-9\-\.]/g,'') || 0);
    const pnls = document.querySelectorAll('.kpi-card .kpi-value');
    pnls.forEach(el=>{
      const val = toNum(el.textContent);
      if (val > 0) el.classList.add('pos');
      if (val < 0) el.classList.add('neg');
    });
  })();
});