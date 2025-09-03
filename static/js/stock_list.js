/* ==========================
   スマホファースト設計、HTML/CSS/JS分けて設計
   タブ切替でセクション中央寄せ
   リロード時も自動中央寄せ
   モーダル内の編集・売却ボタンを下部に横並び
   カード左スワイプでアクション表示、右スワイプで閉じる
========================== */

document.addEventListener("DOMContentLoaded", () => {
  // -------------------------------
  // タブ & セクション
  // -------------------------------
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));
  if (!wrapper || sections.length === 0) return;

  const scrollToSectionCenter = (index, smooth = true) => {
    const target = sections[index];
    if (!target) return;
    const wrapperWidth = wrapper.clientWidth;
    const sectionRect = target.getBoundingClientRect();
    const wrapperRect = wrapper.getBoundingClientRect();
    const sectionLeftRelative = sectionRect.left - wrapperRect.left + wrapper.scrollLeft;
    let scrollLeft = sectionLeftRelative - (wrapperWidth / 2) + (sectionRect.width / 2);
    const maxScroll = wrapper.scrollWidth - wrapperWidth;
    scrollLeft = Math.min(Math.max(scrollLeft, 0), maxScroll);
    wrapper.scrollTo({ left: scrollLeft, behavior: smooth ? "smooth" : "auto" });
  };

  const setActiveTab = index => {
    tabs.forEach(t => t.classList.remove("active"));
    if (tabs[index]) tabs[index].classList.add("active");
    scrollToSectionCenter(index);
    localStorage.setItem("activeBrokerIndex", index);
  };

  const savedIndex = parseInt(localStorage.getItem("activeBrokerIndex"), 10);
  setTimeout(() => setActiveTab(isNaN(savedIndex) ? 0 : savedIndex), 80);

  tabs.forEach((tab, i) => tab.addEventListener("click", () => setActiveTab(i)));

  // -------------------------------
  // モーダル共通関数
  // -------------------------------
  const setupModal = modalId => {
    const modal = document.getElementById(modalId);
    const closeBtn = modal?.querySelector(".modal-close");
    const close = () => modal && (modal.style.display = "none") && modal.setAttribute("aria-hidden", "true");
    closeBtn?.addEventListener("click", close);
    modal?.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && modal.style.display === "block") close(); });
    return modal;
  };

  const stockModal = setupModal("stock-modal");
  const editModal = setupModal("edit-modal");
  const sellModal = setupModal("sell-modal");

  const escapeHTML = str => String(str).replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );

  // -------------------------------
  // 株カードクリック
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    const cardId = card.dataset.id;
    card.addEventListener("click", () => {
      if (card.classList.contains("swiped")) return;
      if (!stockModal) return;
      const modalBody = stockModal.querySelector("#modal-body");
      const modalEditBtn = stockModal.querySelector("#edit-stock-btn");
      const modalSellBtn = stockModal.querySelector("#sell-stock-btn");

      modalBody.innerHTML = `
        <h3 id="modal-title">${escapeHTML(card.dataset.name)} (${escapeHTML(card.dataset.ticker)})</h3>
        <p>株数: ${escapeHTML(card.dataset.shares)}</p>
        <p>取得単価: ¥${escapeHTML(card.dataset.unit_price)}</p>
        <p>現在株価: ¥${escapeHTML(card.dataset.current_price)}</p>
        <p>損益: ¥${escapeHTML(card.dataset.profit)} (${escapeHTML(card.dataset.profit_rate)}%)</p>
      `;
      stockModal.style.display = "block";
      stockModal.setAttribute("aria-hidden", "false");

      modalEditBtn.dataset.id = cardId;
      modalSellBtn.dataset.id = cardId;
    });

    card.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); } });
  });

  // -------------------------------
  // 編集・売却モーダル開閉
  // -------------------------------
  const openEditModal = stock => {
    if (!editModal) return;
    const form = editModal.querySelector("#edit-form");
    form.elements["stock_id"].value = stock.id || "";
    form.elements["name"].value = stock.name || "";
    form.elements["ticker"].value = stock.ticker || "";
    form.elements["shares"].value = stock.shares || "";
    form.elements["unit_price"].value = stock.unit_price || "";
    form.elements["account"].value = stock.account || "";
    form.elements["position"].value = stock.position || "";
    editModal.style.display = "block";
    editModal.setAttribute("aria-hidden", "false");
  };

  const openSellModal = stock => {
    if (!sellModal) return;
    const form = sellModal.querySelector("#sell-form");
    form.elements["stock_id"].value = stock.id || "";
    form.elements["name"].value = stock.name || "";
    form.elements["shares"].value = stock.shares || "";
    sellModal.style.display = "block";
    sellModal.setAttribute("aria-hidden", "false");
  };

  // -------------------------------
  // モーダル内ボタン
  // -------------------------------
  stockModal?.querySelector("#edit-stock-btn")?.addEventListener("click", e => {
    e.stopPropagation();
    const stockId = e.target.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${stockId}']`);
    if (!card) return;
    openEditModal({
      id: card.dataset.id,
      name: card.dataset.name,
      ticker: card.dataset.ticker,
      shares: card.dataset.shares,
      unit_price: card.dataset.unit_price,
      account: card.dataset.account,
      position: card.dataset.position
    });
  });

  stockModal?.querySelector("#sell-stock-btn")?.addEventListener("click", e => {
    e.stopPropagation();
    const stockId = e.target.dataset.id;
    const card = document.querySelector(`.stock-card[data-id='${stockId}']`);
    if (!card) return;
    openSellModal({
      id: card.dataset.id,
      name: card.dataset.name,
      shares: card.dataset.shares
    });
  });

  // -------------------------------
  // カード横スワイプ + アクション
  // -------------------------------
  document.querySelectorAll(".stock-card").forEach(card => {
    let startX=0, startY=0, isDragging=false;
    let actions = card.querySelector(".card-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "card-actions";
      actions.innerHTML = `<button class="edit-btn">編集</button><button class="sell-btn">売却</button>`;
      card.appendChild(actions);
    }

    card.addEventListener("touchstart", e => { const t = e.touches[0]; startX=t.pageX; startY=t.pageY; isDragging=true; }, { passive:true });
    card.addEventListener("touchend", e => {
      if (!isDragging) return;
      isDragging=false;
      const t = e.changedTouches[0];
      const deltaX = t.pageX - startX;
      const deltaY = t.pageY - startY;
      if (Math.abs(deltaY) > Math.abs(deltaX)) return;
      if (deltaX < -50) card.classList.add("swiped");
      else if (deltaX > 50) card.classList.remove("swiped");
    }, { passive:true });

    card.querySelector(".edit-btn")?.addEventListener("click", e => { e.stopPropagation(); openEditModal({
      id: card.dataset.id, name: card.dataset.name, ticker: card.dataset.ticker,
      shares: card.dataset.shares, unit_price: card.dataset.unit_price,
      account: card.dataset.account, position: card.dataset.position
    }); });

    card.querySelector(".sell-btn")?.addEventListener("click", e => { e.stopPropagation(); openSellModal({
      id: card.dataset.id, name: card.dataset.name, shares: card.dataset.shares
    }); });
  });

  // -------------------------------
  // フォーム送信
  // -------------------------------
  editForm?.addEventListener("submit", e => { e.preventDefault(); console.log("編集フォーム送信:", Object.fromEntries(new FormData(editForm).entries())); editModal.style.display="none"; });
  sellForm?.addEventListener("submit", e => { e.preventDefault(); console.log("売却フォーム送信:", Object.fromEntries(new FormData(sellForm).entries())); sellModal.style.display="none"; });
});