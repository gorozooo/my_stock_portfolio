document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");

  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");

  const modalSellBtn = document.getElementById("sell-btn-modal");
  const modalEditBtn = document.getElementById("edit-btn-modal");

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

  // ===== 各カードにイベントを付与 =====
  document.querySelectorAll(".stock-card-wrapper").forEach(wrapper => {
    const card = wrapper.querySelector(".stock-card");
    const sellBtn = wrapper.querySelector(".sell-btn");
    const editBtn = wrapper.querySelector(".edit-btn");

    const stockId = card.dataset.id;
    const name = card.dataset.name;
    const ticker = card.dataset.ticker;
    const shares = Number(card.dataset.shares) || 0;
    const unitPrice = Number(card.dataset.unit_price) || 0;
    let currentPrice = Number(card.dataset.current_price) || 0;
    let profit = Number(card.dataset.profit) || 0;

    let chartHistory = [];
    try { chartHistory = JSON.parse(card.dataset.chart || "[]"); } catch { chartHistory = []; }

    const priceElem = card.querySelector(".stock-row:nth-child(4) span:last-child");
    if (priceElem) priceElem.textContent = `${currentPrice.toLocaleString()}円`;

    const profitElem = card.querySelector(".stock-row.gain span:last-child");
    if (profitElem) {
      profitElem.textContent = `${profit >= 0 ? "+" : ""}${profit.toLocaleString()}円 (${card.dataset.profit_rate}%)`;
      card.querySelector(".stock-row.gain").className = `stock-row gain ${profit >= 0 ? "positive" : "negative"}`;
    }

    // ===== カードクリックでモーダル表示 =====
    card.addEventListener("click", () => {
      modal.style.display = "block";
      modalName.textContent = name;
      modalCode.textContent = ticker;
      modalShares.textContent = `${shares}株`;
      modalCost.textContent = `¥${unitPrice.toLocaleString()}`;
      modalPrice.textContent = `¥${currentPrice.toLocaleString()}`;
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

      // ===== モーダル内ボタンの個別設定 =====
      modalSellBtn.onclick = async () => {
        if (!confirm(`${name} を売却しますか？`)) return;
        try {
          const response = await fetch(`/stocks/${stockId}/sell/`, {
            method: "POST",
            headers: { "X-CSRFToken": getCookie("csrftoken") }
          });
          if (response.ok) {
            wrapper.remove();
            showToast(`✅ ${name} を売却しました！`);
            modal.style.display = "none";
          } else showToast("❌ 売却に失敗しました");
        } catch (err) { console.error(err); showToast("⚠️ 通信エラー"); }
      };

      modalEditBtn.onclick = () => {
        showToast(`✏️ ${name} を編集します（未実装）`);
      };
    });

    // ===== カード内ボタン =====
    if (sellBtn) {
      sellBtn.addEventListener("click", async e => {
        e.stopPropagation();
        if (!confirm(`${name} を売却しますか？`)) return;
        try {
          const response = await fetch(`/stocks/${stockId}/sell/`, {
            method: "POST",
            headers: { "X-CSRFToken": getCookie("csrftoken") }
          });
          if (response.ok) {
            wrapper.remove();
            showToast(`✅ ${name} を売却しました！`);
          } else showToast("❌ 売却に失敗しました");
        } catch (err) { console.error(err); showToast("⚠️ 通信エラー"); }
      });
    }

    if (editBtn) {
      editBtn.addEventListener("click", e => {
        e.stopPropagation();
        showToast(`✏️ ${name} を編集します（未実装）`);
      });
    }

    // ===== スワイプ判定 =====
    let startX = 0;
    let startY = 0;
    let moved = false;
    let currentTranslate = 0;

    card.addEventListener("touchstart", e => {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      moved = false;
    });

    card.addEventListener("touchmove", e => {
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (Math.abs(dx) > Math.abs(dy)) {
        e.preventDefault(); // 横スクロール抑制
        moved = true;
        currentTranslate = Math.min(0, Math.max(-100, dx)); // 左に最大100px
        wrapper.style.transform = `translateX(${currentTranslate}px)`;
      }
    }, { passive: false });

    card.addEventListener("touchend", e => {
      if (!moved) return;
      if (currentTranslate < -50) {
        wrapper.style.transform = `translateX(-100px)`; // アクションボタン表示
      } else {
        wrapper.style.transform = `translateX(0)`; // 元に戻す
      }
    });
  });

  // ===== モーダル閉じる =====
  const closeModal = () => { modal.style.display = "none"; };
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  modal.addEventListener("touchstart", e => { if (e.target === modal) closeModal(); });
});