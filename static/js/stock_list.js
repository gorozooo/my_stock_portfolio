document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");

  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");

  let chartInstance = null;

  // ===== トースト表示関数 =====
  function showToast(message, duration = 2000) {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), duration);
  }

  // ===== CSRFトークン取得関数 =====
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + "=")) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  // ===== 株価取得関数 =====
  async function fetchStockPrice(ticker) {
    try {
      const response = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}.T?interval=1d`);
      if (!response.ok) return null;
      const data = await response.json();
      const price = data.chart.result[0].meta.regularMarketPrice;
      return Number(price);
    } catch (error) {
      console.error("株価取得エラー:", ticker, error);
      return null;
    }
  }

  // ===== 各カードにイベントを付与 =====
  document.querySelectorAll(".stock-card-wrapper").forEach(async wrapper => {
    const card = wrapper.querySelector(".stock-card");
    const sellBtn = wrapper.querySelector(".sell-btn");
    const editBtn = wrapper.querySelector(".edit-btn");

    const stockId = card.dataset.id;
    const name = card.dataset.name;
    const ticker = card.dataset.ticker;
    const shares = Number(card.dataset.shares) || 0;
    const unitPrice = Number(card.dataset.unit_price) || 0;
    let currentPrice = Number(card.dataset.current_price) || 0;
    const profit = Number(card.dataset.profit) || 0;

    let chartHistory = [];
    try { chartHistory = JSON.parse(card.dataset.chart || "[]"); } catch { chartHistory = []; }

    // ===== カードにローディング表示 =====
    const priceElem = card.querySelector(".stock-row:nth-child(4) span:last-child");
    if (priceElem) priceElem.textContent = "取得中…";

    // ===== 最新株価を取得してカード表示更新 =====
    const latestPrice = await fetchStockPrice(ticker);
    if (latestPrice !== null) {
      currentPrice = latestPrice;
      card.dataset.current_price = currentPrice;
      if (priceElem) priceElem.textContent = `${currentPrice.toLocaleString()}円`;

      // 損益更新
      const profitAmount = (currentPrice - unitPrice) * shares;
      const profitRate = unitPrice ? (profitAmount / (unitPrice * shares)) * 100 : 0;
      card.dataset.profit = profitAmount;
      card.dataset.profit_rate = profitRate.toFixed(2);

      const profitElem = card.querySelector(".stock-row.gain span:last-child");
      if (profitElem) profitElem.textContent = `${profitAmount >= 0 ? "+" : ""}${profitAmount.toLocaleString()}円 (${profitRate.toFixed(2)}%)`;
      card.querySelector(".stock-row.gain").className = `stock-row gain ${profitAmount >= 0 ? "positive" : "negative"}`;
    } else {
      if (priceElem) priceElem.textContent = "取得失敗";
    }

    // ===== カードクリックでモーダル表示 =====
    card.addEventListener("click", () => {
      modal.style.display = "block";
      modalName.textContent = name;
      modalCode.textContent = ticker;
      modalShares.textContent = `${shares}株`;
      modalCost.textContent = `¥${unitPrice.toLocaleString()}`;
      modalPrice.textContent = latestPrice !== null ? `¥${currentPrice.toLocaleString()}` : "取得失敗";
      modalProfit.textContent = `${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円`;
      modalProfit.className = profit >= 0 ? "positive" : "negative";

      // ===== チャート描画 =====
      if (chartInstance) chartInstance.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");
      chartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: chartHistory.length ? chartHistory.map((_, i) => `T${i + 1}`) : ["1","2","3","4"],
          datasets: [{
            label: name,
            data: chartHistory.length ? chartHistory : [100,110,105,120],
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

    // ===== 売却ボタン =====
    sellBtn.addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm(`${name} を売却しますか？`)) return;

      try {
        const response = await fetch(`/stocks/${stockId}/sell/`, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCookie("csrftoken"),
            "Content-Type": "application/json"
          }
        });

        if (response.ok) {
          wrapper.remove();
          showToast(`✅ ${name} を売却しました！`);
          modal.style.display = "none";
        } else {
          showToast("❌ 売却に失敗しました");
        }
      } catch (error) {
        console.error(error);
        showToast("⚠️ 通信エラーが発生しました");
      }
    });

    // ===== 編集ボタン =====
    if (editBtn) {
      editBtn.addEventListener("click", e => {
        e.stopPropagation();
        showToast(`✏️ ${name} を編集します（未実装）`);
      });
    }

    // ===== 左スワイプでアクションボタン表示 =====
    let startX = 0;
    card.addEventListener("touchstart", e => { startX = e.touches[0].clientX; });
    card.addEventListener("touchend", e => {
      const endX = e.changedTouches[0].clientX;
      if (startX - endX > 50) wrapper.classList.add("show-actions");
      else if (endX - startX > 50) wrapper.classList.remove("show-actions");
    });
  });

  // ===== モーダル閉じる =====
  const closeModal = () => { modal.style.display = "none"; };
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  modal.addEventListener("touchstart", e => { if (e.target === modal) closeModal(); });
});