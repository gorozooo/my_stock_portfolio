document.addEventListener("DOMContentLoaded", () => {
  const dataEl = document.getElementById("home-data");
  let data = { total_mv: 0, cash_bars: [] };
  try { data = JSON.parse(dataEl.textContent); } catch(e){}

  // キャッシュフロー棒グラフ（今月）
  const cashCanvas = document.getElementById("cashflowChart");
  if (cashCanvas && window.Chart) {
    const labels = (data.cash_bars || []).map(x=>x.label);
    const values = (data.cash_bars || []).map(x=>x.value);
    new Chart(cashCanvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label: "今月", data: values, borderWidth: 1 }] },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
    });
  }

  // AIボタン（将来：GPT接続）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）：総資産の前週比、実現/配当の内訳、偏り指摘を要約。");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手（AI接続予定）：信用比率・評価偏り・現金余力からリバランス案を提示。");
  });
});