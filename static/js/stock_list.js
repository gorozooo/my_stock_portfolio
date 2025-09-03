/* ==========================
   スマホファースト設計、HTML/CSS/JS分けて設計
   タブ切替でセクションを中央寄せ
   リロード時も自動中央寄せ
   モーダル内の編集・売却ボタンを下部に横並び
   DBの値をモーダルフォームに反映して編集可能
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

  // モーダル内フォームを生成
  modalBody.innerHTML = `
    <form id="stock-edit-form">
      <input type="hidden" name="stock_id" id="stock-id">
      <div class="form-row">
        <label for="stock-name">銘柄名</label>
        <input type="text" id="stock-name" name="name" readonly>
      </div>
      <div class="form-row">
        <label for="stock-shares">株数</label>
        <input type="number" id="stock-shares" name="shares" min="1" required>
      </div>
      <div class="form-row">
        <label for="stock-unit-price">取得単価</label>
        <input type="number" id="stock-unit-price" name="unit_price" min="1" required>
      </div>
      <div class="modal-actions">
        <button type="submit" class="modal-action-btn">保存</button>
        <button type="button" id="modal-cancel-btn" class="modal-action-btn negative">キャンセル</button>
      </div>
    </form>
  `;

  const modalForm = document.getElementById("stock-edit-form");
  const modalCancel = document.getElementById("modal-cancel-btn");

  const openModal = stockData => {
    document.getElementById("stock-id").value = stockData.id;
    document.getElementById("stock-name").value = stockData.name;
    document.getElementById("stock-shares").value = stockData.shares;
    document.getElementById("stock-unit-price").value = stockData.unit_price;

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

    // カードクリックでモーダル表示
    card.addEventListener("click", e => {
      if (e.target.closest(".card-actions")) return; // ボタンは別処理
      openModal(getStockData());
    });

    // カード内ボタン追加（スワイプ対応）
    if (!card.querySelector(".card-actions")) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `
        <button class="edit-btn" type="button">編集</button>
        <button class="sell-btn" type="button">売却</button>
      `;
      card.appendChild(actions);
    }

    // カード内「編集ボタン」でモーダル表示
    card.querySelector(".edit-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      openModal(getStockData());
    });

    // カード内「売却ボタン」
    card.querySelector(".sell-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      console.log(`カード内 売却ボタン押下 ID=${card.dataset.id}`);
      // TODO: 売却処理
    });
  });

  // -------------------------------
  // モーダルフォーム送信
  // -------------------------------
  modalForm?.addEventListener("submit", e => {
    e.preventDefault();
    const formData = new FormData(modalForm);
    const stockId = formData.get("stock_id");
    const shares = formData.get("shares");
    const unit_price = formData.get("unit_price");

    console.log(`保存: ID=${stockId}, 株数=${shares}, 取得単価=${unit_price}`);
    // TODO: AjaxでDB更新処理
    // 更新後にカード表示も書き換える場合はDOMを更新

    closeModal();
  });
});