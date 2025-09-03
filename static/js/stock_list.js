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
  const modalForm = document.getElementById("stock-edit-form");
  const modalClose = document.querySelector(".modal-close");
  const modalCancel = document.getElementById("modal-cancel-btn");

  const openModal = stockData => {
    modalForm.stock_id.value = stockData.id;
    modalForm.name.value = stockData.name;
    modalForm.shares.value = stockData.shares;
    modalForm.unit_price.value = stockData.unit_price;

    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");
  };

  const closeModal = () => {
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
  };

  modalClose?.addEventListener("click", closeModal);
  modalCancel?.addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && modal.style.display === "block") closeModal(); });

  // -------------------------------
  // カードクリック＆カード内編集ボタン
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    const getStockData = () => ({
      id: card.dataset.id,
      name: card.dataset.name,
      shares: card.dataset.shares,
      unit_price: card.dataset.unit_price
    });

    // カード自体をクリックしたらモーダル表示
    card.addEventListener("click", e => {
      if (e.target.closest(".card-actions")) return; // ボタンは別処理
      openModal(getStockData());
    });

    // カード内ボタンを生成（スマホスワイプ対応も）
    if (!card.querySelector(".card-actions")) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn" type="button">編集</button>
        <button class="sell-btn" type="button">売却</button>
      `;
      card.appendChild(actions);
    }

    // カード内「編集ボタン」押下でモーダル表示
    card.querySelector(".edit-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      openModal(getStockData());
    });

    // カード内「売却ボタン」押下
    card.querySelector(".sell-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      console.log(`カード内 売却ボタン押下 ID=${card.dataset.id}`);
      // TODO: 売却処理
    });

    // スワイプ判定（横スワイプでカード表示切替など）
    let startX = 0, startY = 0, isDragging = false;
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
  });

  // -------------------------------
  // モーダル内フォーム送信
  // -------------------------------
  modalForm?.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(modalForm);
    const stockId = formData.get("stock_id");
    const name = formData.get("name");
    const shares = formData.get("shares");
    const unit_price = formData.get("unit_price");

    console.log(`編集送信 ID=${stockId}, name=${name}, shares=${shares}, unit_price=${unit_price}`);
    // TODO: Ajaxで保存 or フォーム送信処理

    closeModal();
  });
});