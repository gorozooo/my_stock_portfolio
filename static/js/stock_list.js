/* スマホファースト設計 、html css jsは分ける*/

document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".broker-tab");
  const wrapper = document.querySelector(".broker-horizontal-wrapper");
  const sections = document.querySelectorAll(".broker-section");

  // 初期表示
  tabs[0].classList.add("active");
  sections.forEach((s, i) => s.style.display = i === 0 ? "flex" : "none");

  // タブクリックで横スクロール切替
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      const index = tab.dataset.brokerIndex;
      sections.forEach((s, i) => s.style.display = i == index ? "flex" : "none");
      sections[index].scrollIntoView({ behavior: "smooth", inline: "start" });
    });
  });

  // 株カードクリックでモーダル表示
  const modal = document.getElementById("stock-modal");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.querySelector(".modal-close");

  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
      const name = card.dataset.name;
      const ticker = card.dataset.ticker;
      const shares = card.dataset.shares;
      const unitPrice = card.dataset.unit_price;
      const currentPrice = card.dataset.current_price;
      const profit = card.dataset.profit;
      const profitRate = card.dataset.profit_rate;

      modalBody.innerHTML = `
        <h3>${name} (${ticker})</h3>
        <p>株数: ${shares}</p>
        <p>取得単価: ¥${unitPrice}</p>
        <p>現在株価: ¥${currentPrice}</p>
        <p>損益: ¥${profit} (${profitRate}%)</p>
      `;
      modal.style.display = "block";
    });
  });

  modalClose.addEventListener("click", () => { modal.style.display = "none"; });
  modal.addEventListener("click", e => { if(e.target === modal) modal.style.display = "none"; });
});