// ===== stock_list.js =====



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
    requestAnimationFrame(() => toast.classList.add("show"));
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
      document.body.style.overflow = "hidden";

      modalName.textContent = name;
      modalCode.textContent = ticker;
      modalShares.textContent = `${shares}株`;
      modalCost.textContent = `¥${unitPrice.toLocaleString()}`;
      modalPrice.textContent = `¥${currentPrice.toLocaleString()}`;
      modalProfit.textContent = `${profit >= 0 ? "+" : ""}${profit.toLocaleString()} 円`;
      modalProfit.className = profit >= 0 ? "positive" : "negative";

      // ===== ローソク足チャート描画 =====
      if (chartInstance) chartInstance.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");

      if (chartHistory.length > 0) {
        const formattedData = chartHistory.map(val => ({
          x: new Date(val.t),
          o: Number(val.o),
          h: Number(val.h),
          l: Number(val.l),
          c: Number(val.c)
        }));

        chartInstance = new Chart(ctx, {
          type: "candlestick",
          data: { datasets: [{ label: name, data: formattedData }] },
          options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
              x: { 
                type: 'time',
                time: { unit: 'day', tooltipFormat: 'yyyy-MM-dd' },
                title: { display: true, text: '日付' } 
              },
              y: { title: { display: true, text: '株価' } }
            }
          }
        });
      } else {
        showToast("⚠️ チャートデータがありません");
      }
    });

    // ===== カード内「売却」「編集」ボタン =====
    sellBtn?.addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm(`${name} を売却しますか？`)) return;
      try {
        const res = await fetch(`/stocks/${stockId}/sell/`, {
          method: "POST",
          headers: { "X-CSRFToken": getCookie("csrftoken") }
        });
        if (res.ok) {
          wrapper.remove();
          showToast(`✅ ${name} を売却しました！`);
        } else showToast("❌ 売却に失敗しました");
      } catch { showToast("⚠️ 通信エラー"); }
    });

    editBtn?.addEventListener("click", e => {
      e.stopPropagation();
      showToast(`✏️ ${name} を編集します（未実装）`);
    });

    // ===== スワイプ判定（スマホ対応） =====
    let startX = 0, startY = 0, moved = false, currentTranslate = 0;

    card.addEventListener("touchstart", e => {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      moved = false;
      wrapper.style.transition = "";
    });

    card.addEventListener("touchmove", e => {
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (Math.abs(dx) > Math.abs(dy)) {
        e.preventDefault();
        moved = true;
        currentTranslate = Math.min(0, Math.max(-160, dx));
        wrapper.style.transform = `translateX(${currentTranslate}px)`;
      }
    }, { passive: false });

    card.addEventListener("touchend", () => {
      if (!moved) return;
      wrapper.style.transition = "transform 0.25s ease";
      if (currentTranslate < -80) {
        wrapper.style.transform = "translateX(-160px)";
        wrapper.classList.add("show-actions");
      } else {
        wrapper.style.transform = "translateX(0)";
        wrapper.classList.remove("show-actions");
      }
    });
  });

  // ===== モーダル閉じる =====
  function closeModal() {
    modal.style.display = "none";
    document.body.style.overflow = "";
    if (chartInstance) chartInstance.destroy();
  }

  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  modal.addEventListener("touchmove", e => e.stopPropagation(), { passive: false });

  // ===== モーダル内「売却」「編集」ボタンのイベント（再登録） =====
  modalSellBtn.addEventListener("click", () => {
    const card = document.querySelector(`.stock-card[data-id="${modal.dataset.id}"]`);
    card?.querySelector(".sell-btn")?.click();
    closeModal();
  });

  modalEditBtn.addEventListener("click", () => showToast("✏️ 編集機能（未実装）"));
});