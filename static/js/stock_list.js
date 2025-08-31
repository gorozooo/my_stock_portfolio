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

    const stockId = card.dataset.id;
    const name = card.dataset.name;
    const code = card.dataset.code;
    const shares = card.dataset.shares;
    const cost = card.dataset.cost;
    const price = card.dataset.price;
    const profit = card.dataset.profit;
    const chartHistory = JSON.parse(card.dataset.chart || "[]");

    // ===== カードクリックでモーダル表示 =====
    card.addEventListener("click", () => {
      modal.style.display = "block";
      modalName.textContent = name;
      modalCode.textContent = code;
      modalShares.textContent = `${shares}株`;
      modalCost.textContent = `¥${Number(cost).toLocaleString()}`;
      modalPrice.textContent = `¥${Number(price).toLocaleString()}`;
      modalProfit.textContent = `${Number(profit) >= 0 ? "+" : ""}${Number(profit).toLocaleString()} 円`;
      modalProfit.className = Number(profit) >= 0 ? "positive" : "negative";

      // ===== チャート描画 =====
      if (chartInstance) chartInstance.destroy();
      const ctx = document.getElementById("modal-chart").getContext("2d");
      chartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: Array.from({ length: chartHistory.length }, (_, i) => `${i + 1}`),
          datasets: [{
            label: name,
            data: chartHistory,
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
  });

  // ===== モーダル閉じる =====
  const closeModal = () => { modal.style.display = "none"; };
  closeBtn.addEventListener("click", closeModal);
  window.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  modal.addEventListener("touchstart", e => { if (e.target === modal) closeModal(); });

  // ===== CSRFトークン取得関数 =====
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith(name + "=")) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }
});