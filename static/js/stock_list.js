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

  let chartInstance = null;

  // ===== カード生成 =====
  stocks.forEach(stock => {
    const profit = (stock.price - stock.cost) * stock.shares;
    const profitClass = profit >= 0 ? "positive" : "negative";

    const cardWrapper = document.createElement("div");
    cardWrapper.className = "stock-card-wrapper";

    cardWrapper.innerHTML = `
      <div class="stock-card">
        <div class="stock-header">
          <span class="stock-name">${stock.name}</span>
          <span class="stock-code">${stock.code}</span>
        </div>
        <div class="stock-row"><span>株数</span><span>${stock.shares}</span></div>
        <div class="stock-row"><span>取得単価</span><span>¥${stock.cost.toLocaleString()}</span></div>
        <div class="stock-row"><span>現在株価</span><span>¥${stock.price.toLocaleString()}</span></div>
        <div class="stock-row gain ${profitClass}"><span>損益</span><span>${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円</span></div>
      </div>
      <button class="sell-btn">売却</button>
    `;

    const card = cardWrapper.querySelector(".stock-card");
    const sellBtn = cardWrapper.querySelector(".sell-btn");
    sellBtn.style.opacity = "0";
    sellBtn.style.pointerEvents = "none";

    // ===== タッチ・スワイプ / PCクリック共通 =====
    let startX = 0;
    let currentX = 0;
    let isSwiping = false;

    // スワイプ開始
    card.addEventListener("touchstart", e => {
      startX = e.touches[0].clientX;
      card.style.transition = "none";
    });

    // スワイプ移動
    card.addEventListener("touchmove", e => {
      currentX = e.touches[0].clientX;
      const diffX = currentX - startX;
      if (diffX < 0) {
        isSwiping = true;
        card.style.transform = `translateX(${diffX}px)`;
        sellBtn.style.opacity = `${Math.min(Math.abs(diffX)/100,1)}`;
      }
    });

    // スワイプ終了
    card.addEventListener("touchend", () => {
      const diffX = currentX - startX;
      if (diffX < -50) {
        card.style.transition = "transform 0.3s ease";
        card.style.transform = "translateX(-100px)";
        sellBtn.style.opacity = "1";
        sellBtn.style.pointerEvents = "auto";
      } else {
        card.style.transition = "transform 0.3s ease";
        card.style.transform = "translateX(0)";
        sellBtn.style.opacity = "0";
        sellBtn.style.pointerEvents = "none";
      }
      isSwiping = false;
    });

    // PCクリックで売却ボタン表示
    card.addEventListener("click", e => {
      if (window.innerWidth >= 768) {
        if (e.target !== sellBtn) {
          card.style.transition = "transform 0.3s ease";
          card.style.transform = "translateX(-100px)";
          sellBtn.style.opacity = "1";
          sellBtn.style.pointerEvents = "auto";
        }
      }
    });

    // カードクリックでモーダル表示
    card.addEventListener("click", e => {
      if (e.target === sellBtn || isSwiping) return;

      modal.style.display = "block";
      modalName.textContent = stock.name;
      modalCode.textContent = stock.code;
      modalShares.textContent = stock.shares;
      modalCost.textContent = `¥${stock.cost.toLocaleString()}`;
      modalPrice.textContent = `¥${stock.price.toLocaleString()}`;
      modalProfit.textContent = `${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円`;
      modalProfit.className = profit >= 0 ? "positive" : "negative";

      if (chartInstance) chartInstance.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");
      chartInstance = new Chart(ctx, {
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
            borderColor: "#00ffff",
            backgroundColor: "rgba(0,255,255,0.2)",
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

    // 売却ボタンクリック
    sellBtn.addEventListener("click", e => {
      e.stopPropagation();
      alert(`✅ ${stock.name} を売却しました（ダミー処理）`);
      cardWrapper.remove();
    });

    container.appendChild(cardWrapper);
  });

  // ===== モーダル閉じる =====
  closeBtn.addEventListener("click", () => { modal.style.display = "none"; });
  window.addEventListener("click", e => { if (e.target === modal) modal.style.display = "none"; });
  modal.addEventListener("touchstart", e => { if (e.target === modal) modal.style.display = "none"; });
});