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
  const scrollToSectionCenter = (index, smooth = true) => {
    const targetSection = sections[index];
    if (!targetSection) return;

    const wrapperWidth = wrapper.clientWidth;
    const sectionRect = targetSection.getBoundingClientRect();
    const wrapperRect = wrapper.getBoundingClientRect();

    // wrapper 内の相対位置
    const sectionLeftRelative = sectionRect.left - wrapperRect.left + wrapper.scrollLeft;

    // 中央寄せ計算
    let scrollLeft = sectionLeftRelative - (wrapperWidth / 2) + (sectionRect.width / 2);

    // スクロール可能範囲に制限
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
  };

  // -------------------------------
  // 初期表示：リロード時も中央寄せ
  // -------------------------------
  const initialIndex = 0; // 最初のタブ
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
      modalClose?.focus();
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
  // 縦スクロールを妨げないスワイプ判定
  // （横方向が優勢な時だけ「編集/売却」を開く）
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

    card.addEventListener("touchmove", e => {
      // ここで preventDefault はしない（縦スクロールを殺さない）
    }, { passive: true });

    card.addEventListener("touchend", e => {
      if (!isDragging) return;
      isDragging = false;

      const t = e.changedTouches[0];
      const deltaX = t.pageX - startX;
      const deltaY = t.pageY - startY;

      // 縦方向の移動が大きい場合はスワイプ判定しない（縦スクロール優先）
      if (Math.abs(deltaY) > Math.abs(deltaX)) return;

      if (deltaX < -50) card.classList.add("swiped");     // 左スワイプで開く
      else if (deltaX > 50) card.classList.remove("swiped"); // 右スワイプで閉じる
    }, { passive: true });

    card.querySelector(".edit-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      alert("編集画面へ移動します");
    });
    card.querySelector(".sell-btn")?.addEventListener("click", e => {
      e.stopPropagation();
      alert("売却処理を実行します");
    });
  });

  // -------------------------------
  // ★重要★ 以前の「カード横スワイプ禁止」コードは削除
  //  cardsWrapper.addEventListener('touchmove', e => e.preventDefault());
  //  これが縦スクロールも殺していました。
  // -------------------------------
});