document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("stock-modal");
  const closeBtn = modal.querySelector(".close");

  const modalName = document.getElementById("modal-name");
  const modalCode = document.getElementById("modal-code");
  const modalShares = document.getElementById("modal-shares");
  const modalCost = document.getElementById("modal-cost");
  const modalPrice = document.getElementById("modal-price");
  const modalProfit = document.getElementById("modal-profit");

  function openModal(e) {
    const card = e.currentTarget;
    modalName.textContent = card.dataset.name;
    modalCode.textContent = card.dataset.code;
    modalShares.textContent = card.dataset.shares;
    modalCost.textContent = card.dataset.cost;
    modalPrice.textContent = card.dataset.price;
    modalProfit.textContent = card.dataset.profit;
    modal.style.display = "block";
  }

  // カードにclickとtouchstart両方追加
  const cards = document.querySelectorAll(".stock-card");
  cards.forEach(card => {
    card.addEventListener("click", openModal);
    card.addEventListener("touchstart", openModal);
  });

  // 閉じるボタン
  closeBtn.addEventListener("click", () => { modal.style.display = "none"; });

  // モーダル外クリックで閉じる
  window.addEventListener("click", (e) => {
    if (e.target == modal) modal.style.display = "none";
  });
});
