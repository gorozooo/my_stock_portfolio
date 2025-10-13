document.addEventListener("DOMContentLoaded", () => {
  // キャッシュフロー：テンプレ側で <script> を埋めてないので、サーバ計算の2本棒だけ描画
  const el = document.getElementById("cashflowChart");
  if (el && window.Chart) {
    const ctx = el.getContext("2d");
    const dataEl = document.getElementById("home-data"); // 互換: あれば使用
    let data = { cash_bars: [] };
    try { if (dataEl) data = JSON.parse(dataEl.textContent); } catch(e){}

    const labels = (data.cash_bars || []).map(x=>x.label);
    const values = (data.cash_bars || []).map(x=>x.value);

    new Chart(ctx, {
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

  // ダミー（将来接続）
  document.getElementById("btn-ai-weekly")?.addEventListener("click", () => {
    alert("週次レポート（AI接続予定）");
  });
  document.getElementById("btn-ai-rebalance")?.addEventListener("click", () => {
    alert("次の一手レコメンド（AI接続予定）");
  });
});