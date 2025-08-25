document.addEventListener("DOMContentLoaded", () => {
  const stocks = [
    { name: "トヨタ", code: "7203", shares: 100, cost: 2100, price: 2300 },
    { name: "ソニーG", code: "6758", shares: 50, cost: 12500, price: 11900 },
    { name: "任天堂", code: "7974", shares: 30, cost: 56000, price: 60000 }
  ];

  const container = document.getElementById("stock-cards-container");
  const modal = document.getElementById("stock-modal");
  const closeBtn = document.querySelector(".close");

  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");

  let chart;

  // ===== カード生成 =====
  stocks.forEach(stock => {
    const profit = (stock.price - stock.cost) * stock.shares;
    const profitClass = profit >= 0 ? "positive" : "negative";

    const card = document.createElement("div");
    card.className = "stock-card";
    card.innerHTML = `
      <div class="stock-header">
        <span class="stock-name">${stock.name}</span>
        <span class="stock-code">${stock.code}</span>
      </div>
      <div class="stock-info">
        <span>株数: ${stock.shares}</span>
        <span>取得: ¥${stock.cost.toLocaleString()}</span>
      </div>
      <div class="stock-info">
        <span>現在: ¥${stock.price.toLocaleString()}</span>
      </div>
      <div class="stock-profit ${profitClass}">
        ${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円
      </div>
    `;

    card.addEventListener("click", () => {
      modal.style.display = "block";
      modalName.textContent = stock.name;
      modalCode.textContent = stock.code;
      modalShares.textContent = stock.shares;
      modalCost.textContent = `¥${stock.cost.toLocaleString()}`;
      modalPrice.textContent = `¥${stock.price.toLocaleString()}`;
      modalProfit.textContent = `${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円`;
      modalProfit.className = profit >= 0 ? "positive" : "negative";

      if (chart) chart.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");
      chart = new Chart(ctx, {
        type: "line",
        data: {
          labels: ["1M", "3M", "6M", "1Y"],
          datasets: [{
            label: stock.name,
            data: [
              stock.cost * 0.9,
              stock.cost,
              stock.price * 0.95,
              stock.price
            ],
            borderColor: "#007aff",
            backgroundColor: "rgba(0,122,255,0.2)",
            fill: true,
            tension: 0.4
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { x: { display: false }, y: { display: false } }
        }
      });
    });

    container.appendChild(card);
  });

  // ===== モーダル閉じる =====
  closeBtn.addEventListener("click", () => {
    modal.style.display = "none";
  });
  window.addEventListener("click", e => {
    if (e.target === modal) modal.style.display = "none";
  });
});