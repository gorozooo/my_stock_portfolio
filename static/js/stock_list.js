/* スマホファースト設計、HTML/CSS/JS分けて設計 */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));

  if (!wrapper || sections.length === 0) return;

  // -------------------------------
  // 最初のタブをアクティブ
  // -------------------------------
  if (tabs.length > 0) tabs[0].classList.add("active");

  // -------------------------------
  // タブクリックで横スクロール切替＆中央表示
  // -------------------------------
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const index = parseInt(tab.dataset.brokerIndex, 10) || 0;

      // タブのアクティブ切替
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      // 対象セクションの最初のカードを中央にスクロール
      const targetSection = sections[index];
      if (targetSection) {
        const cardWrapper = targetSection.querySelector(".broker-cards-wrapper");
        if (cardWrapper) {
          const firstCard = cardWrapper.querySelector(".stock-card");
          if (firstCard) {
            const scrollLeft = firstCard.offsetLeft - (wrapper.clientWidth / 2) + (firstCard.offsetWidth / 2);
            wrapper.scrollTo({ left: scrollLeft, behavior: "smooth" });
          }
        }
      }
    });
  });

  // -------------------------------
  // モーダル関連
  // -------------------------------
  const modal = document.getElementById("stock-modal");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.querySelector(".modal-close");

  const escapeHTML = str => String(str).replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );

  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
      if (card.classList.contains("swiped")) return;

      const name = escapeHTML(card.dataset.name || "");
      const ticker = escapeHTML(card.dataset.ticker || "");
      const shares = escapeHTML(card.dataset.shares || "");
      const unitPrice = escapeHTML(card.dataset.unit_price || "");
      const currentPrice = escapeHTML(card.dataset.current_price || "");
      const profit = escapeHTML(card.dataset.profit || "");
      const profitRate = escapeHTML(card.dataset.profit_rate || "");

      modalBody.innerHTML = `
        <h3 id="modal-title">${name} (${ticker})</h3>
        <p>株数: ${shares}</p>
        <p>取得単価: ¥${unitPrice}</p>
        <p>現在株価: ¥${currentPrice}</p>
        <p>損益: ¥${profit} (${profitRate}%)</p>
      `;
      modal.style.display = "block";
      modal.setAttribute("aria-hidden", "false");
      modalClose.focus();
    });

    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        card.click();
      }
    });
  });

  const closeModal = () => {
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
  };

  modalClose.addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && modal.style.display === "block") closeModal(); });

  // -------------------------------
  // カード横スワイプ禁止（タブ切替のみ中央表示）
  // -------------------------------
  document.querySelectorAll(".broker-cards-wrapper").forEach(cardsWrapper => {
    cardsWrapper.addEventListener("touchmove", e => {
      e.stopPropagation();
      e.preventDefault(); // 横スクロール禁止
    }, { passive: false });
  });

  // -------------------------------
  // カード左スワイプで「編集」「売却」を表示、右スワイプで閉じる
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX = 0;

    if (!card.querySelector(".card-actions")) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn">編集</button>
        <button class="sell-btn">売却</button>
      `;
      card.appendChild(actions);
    }

    card.addEventListener("touchstart", e => { startX = e.touches[0].pageX; });

    card.addEventListener("touchend", e => {
      const endX = e.changedTouches[0].pageX;
      const deltaX = endX - startX;

      if (deltaX < -50) card.classList.add("swiped"); 
      else if (deltaX > 50) card.classList.remove("swiped"); 
    });

    card.querySelector(".edit-btn").addEventListener("click", e => { e.stopPropagation(); alert("編集画面へ移動します"); });
    card.querySelector(".sell-btn").addEventListener("click", e => { e.stopPropagation(); alert("売却処理を実行します"); });
  });
});