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
    let toast = document.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), duration);
  }

  // ===== 各カードにイベントを付与 =====
  document.querySelectorAll(".stock-card-wrapper").forEach(wrapper => {
    const card = wrapper.querySelector(".stock-card");
    const sellBtn = wrapper.querySelector(".sell-btn");
    const editBtn = wrapper.querySelector(".edit-btn");

    // dataset に id が入っている想定
    const stockId = card.dataset.id;

    // ===== カードクリックでモーダル表示 =====
    card.addEventListener("click", () => {
      const name = card.querySelector(".stock-name").textContent;
      const code = card.querySelector(".stock-code").textContent;
      const shares = card.querySelector(".stock-row:nth-child(2) span:last-child").textContent;
      const cost = card.querySelector(".stock-row:nth-child(3) span:last-child").textContent;
      const price = card.querySelector(".stock-row:nth-child(4) span:last-child").textContent;
      const profitText = card.querySelector(".gain span:last-child").textContent;
      const profitClass = card.querySelector(".gain").classList.contains("positive") ? "positive" : "negative";

      modal.style.display = "block";
      modalName.textContent = name;
      modalCode.textContent = code;
      modalShares.textContent = shares;
      modalCost.textContent = cost;
      modalPrice.textContent = price;
      modalProfit.textContent = profitText;
      modalProfit.className = profitClass;

      // ===== チャート描画（簡易ダミーデータ） =====
      if (chartInstance) chartInstance.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");
      chartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: ["1M", "3M", "6M", "1Y"],
          datasets: [{
            label: name,
            data: [100, 120, 110, 130], // ← 実際の履歴データに差し替え可能
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
    sellBtn.addEventListener("click", e => {
      e.stopPropagation();
      wrapper.remove();
      showToast(`✅ ${card.querySelector(".stock-name").textContent} を売却しました！`);
    });

    // ===== 編集ボタン =====
    if (editBtn) {
      editBtn.addEventListener("click", e => {
        e.stopPropagation();
        showToast(`✏️ ${card.querySelector(".stock-name").textContent} を編集します（未実装）`);
      });
    }
  });

  // ===== モーダル閉じる =====
  const closeModal = () => { modal.style.display = "none"; };
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  modal.addEventListener("touchstart", e => { if (e.target === modal) closeModal(); });
});