/* ==========================
   スマホファースト設計、HTML/CSS/JS分けて設計
   タブ切替でセクションを中央寄せ
   リロード時も自動中央寄せ
   モーダル内の編集・売却ボタンを下部に横並び
   ========================== */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));

  if (!wrapper || sections.length === 0) return;

  // -------------------------------
  // セクションを中央にスクロールする関数
  // -------------------------------
  const scrollToSectionCenter = (index, smooth = true) => {
    const targetSection = sections[index];
    if (!targetSection) return;

    const wrapperWidth = wrapper.clientWidth;
    const sectionRect = targetSection.getBoundingClientRect();
    const wrapperRect = wrapper.getBoundingClientRect();

    const sectionLeftRelative = sectionRect.left - wrapperRect.left + wrapper.scrollLeft;
    let scrollLeft = sectionLeftRelative - (wrapperWidth / 2) + (sectionRect.width / 2);

    const maxScroll = wrapper.scrollWidth - wrapperWidth;
    scrollLeft = Math.min(Math.max(scrollLeft, 0), maxScroll);

    wrapper.scrollTo({ left: scrollLeft, behavior: smooth ? "smooth" : "auto" });
  };

  // -------------------------------
  // タブアクティブ設定＆中央寄せ
  // -------------------------------
  const setActiveTab = index => {
    tabs.forEach(t => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    scrollToSectionCenter(index, true);
    localStorage.setItem("activeBrokerIndex", index);
  };

  // -------------------------------
  // 初期表示：前回のタブ or 最初のタブ
  // -------------------------------
  const savedIndex = parseInt(localStorage.getItem("activeBrokerIndex"), 10);
  const initialIndex = isNaN(savedIndex) ? 0 : savedIndex;
  setTimeout(() => setActiveTab(initialIndex), 80);

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
  const modalEditBtn = document.getElementById("edit-stock-btn");
  const modalSellBtn = document.getElementById("sell-stock-btn");

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

      // モーダル内ボタンに株データを渡す
      modalEditBtn.dataset.id = card.dataset.id;
      modalSellBtn.dataset.id = card.dataset.id;
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

  modalClose?.addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && modal.style.display === "block") closeModal(); });

  // -------------------------------
  // モーダル内「編集・売却」ボタン
  // -------------------------------
  modalEditBtn?.addEventListener("click", e => {
    e.stopPropagation();
    console.log(`モーダル内 編集ボタン押下 ID=${modalEditBtn.dataset.id}`);
    // TODO: 編集画面へ遷移処理
  });

  modalSellBtn?.addEventListener("click", e => {
    e.stopPropagation();
    console.log(`モーダル内 売却ボタン押下 ID=${modalSellBtn.dataset.id}`);
    // TODO: 売却処理
  });

  // -------------------------------
  // 縦スクロールを妨げないカード横スワイプ判定
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX = 0, startY = 0, isDragging = false;

    if (!card.querySelector(".card-actions")) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn">編集</button>
        <button class="sell-btn">売却</button>
      `;
      card.appendChild(actions);
    }

    card.addEventListener("touchstart", e => {
      const t = e.touches[0];
      startX = t.pageX;
      startY = t.pageY;
      isDragging = true;
    }, { passive: true });

    card.addEventListener("touchend", e => {
      if (!isDragging) return;
      isDragging = false;

      const t = e.changedTouches[0];
      const deltaX = t.pageX - startX;
      const deltaY = t.pageY - startY;

      if (Math.abs(deltaY) > Math.abs(deltaX)) return;

      if (deltaX < -50) card.classList.add("swiped");
      else if (deltaX > 50) card.classList.remove("swiped");
    }, { passive: true });

    // カード内ボタンイベント
    card.querySelector(".edit-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      console.log("カード内 編集ボタン押下");
    });
    card.querySelector(".sell-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      console.log("カード内 売却ボタン押下");
    });
  });
});

このコードを対応するように修正して全文送って