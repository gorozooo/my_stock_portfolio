document.addEventListener("DOMContentLoaded", () => {
  // データ受け取り
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, cash_bars: [] };
  try { data = JSON.parse(dataEl.textContent); } catch(e){}

  // ストレステスト
  const pctEl = document.getElementById("stressPct");
  const mvEl  = document.getElementById("stressMV");
  const slider = document.getElementById("stressSlider");
  // 評価総資産を使って推定（beta=0.9はviewsと合わせる）
  const totalMV = Number(document.querySelector(".kpi-card .kpi-value")?.textContent.replace(/[^0-9]/g,'') || 0);
  const beta = 0.9;

  const updateStress = () => {
    const pct = Number(slider.value);
    const mv = Math.round(totalMV * (1 + beta * pct/100));
    pctEl.textContent = String(pct);
    mvEl.textContent = "¥" + mv.toLocaleString();
  };
  if (slider) {
    slider.addEventListener("input", updateStress);
    updateStress();
  }

  // キャッシュフロー（今月）
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = data.cash_bars.map(x=>x.label);
    const values = data.cash_bars.map(x=>x.value);
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

  // AIボタン（ダミー）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）: 直近の推移・勝率・強弱セクターを要約します。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手（AI接続予定）: 現金比率・含み益・セクター偏りから提案します。");
  });
});