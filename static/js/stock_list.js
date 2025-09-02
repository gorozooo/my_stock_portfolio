/* ==========================
   スマホファースト設計、HTML/CSS/JS分けて設計
   タブ切替でセクションを中央寄せ
   リロード時も自動中央寄せ
   ========================== */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));

  if (!wrapper || sections.length === 0) return;

  // -------------------------------
  // セクションを中央にスクロールする関数
  // -------------------------------
  const scrollToSectionCenter = index => {
    const targetSection = sections[index];
    if (!targetSection) return;

    const wrapperWidth = wrapper.clientWidth;
    const sectionWidth = targetSection.offsetWidth;

    // セクションの左端を取得
    const sectionLeft = targetSection.offsetLeft;

    // 中央寄せ計算
    let scrollLeft = sectionLeft - (wrapperWidth / 2) + (sectionWidth / 2);

    // スクロール可能範囲に制限
    const maxScroll = wrapper.scrollWidth - wrapperWidth;
    scrollLeft = Math.min(Math.max(scrollLeft, 0), maxScroll);

    wrapper.scrollTo({ left: scrollLeft, behavior: "smooth" });
  };

  // -------------------------------
  // タブアクティブ設定＆中央寄せ
  // -------------------------------
  const setActiveTab = index => {
    tabs.forEach(t => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    scrollToSectionCenter(index);
  };

  // -------------------------------
  // 初期表示：リロード時も中央寄せ
  // -------------------------------
  const initialIndex = 0; // 最初のタブ
  // 少し遅延させると正確に中央寄せされやすい
  setTimeout(() => setActiveTab(initialIndex), 50);

  // -------------------------------
  // タブクリック
  // -------------------------------
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => setActiveTab(index));
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