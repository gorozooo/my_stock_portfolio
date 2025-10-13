document.addEventListener("DOMContentLoaded", () => {
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, sectors: [], cash_bars: [] };
  try { data = JSON.parse(dataEl.textContent); } catch(e){}

  // キャッシュフロー棒グラフ
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

  // AIボタン（将来：GPT接続）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）。直近の推移・勝率・強弱セクターを要約します。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手レコメンド（AI接続予定）。現金比率・含み益・セクター偏りから提案します。");
  });
});