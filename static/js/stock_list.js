document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");
  const sellBtn = document.getElementById("sell-btn");

  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");
  const chartCanvas = document.getElementById("modal-chart");
  let chartInstance = null;

  const cards = document.querySelectorAll(".stock-card");

  function openModal(e) {
    const card = e.currentTarget;
    modalName.textContent = card.dataset.name;
    modalCode.textContent = card.dataset.code;
    modalShares.textContent = card.dataset.shares;
    modalCost.textContent = card.dataset.cost;
    modalPrice.textContent = card.dataset.price;
    modalProfit.textContent = card.dataset.profit;

    const chartData = JSON.parse(card.dataset.chart);
    if(chartInstance) chartInstance.destroy();
    chartInstance = new Chart(chartCanvas, {
      type: 'line',
      data: {
        labels: chartData.map((_, i) => i + 1),
        datasets: [{
          label: '株価推移',
          data: chartData,
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.2)',
          tension: 0.3
        }]
      },
      options: {
        responsive:true,
        plugins:{legend:{display:false}},
        scales:{y:{beginAtZero:false}}
      }
    });

    modal.style.display = "block";
  }

  // カードクリック/タッチでモーダル表示
  cards.forEach(card => {
    card.addEventListener("click", openModal);
    card.addEventListener("touchstart", openModal);
  });

  // モーダル閉じる
  closeBtn.addEventListener("click", () => { modal.style.display = "none"; });
  window.addEventListener("click", (e) => { if(e.target==modal) modal.style.display="none"; });

  // ダミー売却処理
  sellBtn.addEventListener("click", () => {
    // アラート表示
    alert(`✅ ${modalName.textContent} を売却しました（ダミー処理）`);

    // モーダル閉じる
    modal.style.display = "none";

    // カードを画面から削除（売却済み風）
    const cardToRemove = Array.from(cards).find(c => c.dataset.name === modalName.textContent);
    if(cardToRemove) cardToRemove.remove();
  });
});
