/* スマホファースト設計、HTML/CSS/JS分けて設計 */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = Array.from(document.querySelectorAll(".broker-tab"));
  const wrapper = document.getElementById("broker-horizontal-wrapper");
  const sections = Array.from(document.querySelectorAll(".broker-section"));

  if (!wrapper || sections.length === 0) return;

  // 最初のタブだけ active に
  if (tabs.length > 0) tabs[0].classList.add("active");

  // タブクリックで横スクロール切替
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const index = parseInt(tab.dataset.brokerIndex, 10) || 0;
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      const target = sections[index];
      if (target) {
        const left = target.offsetLeft;
        wrapper.scrollTo({ left, behavior: "smooth" });
      }
    });
  });

  // 横スクロール時にアクティブタブを更新
  let scrollTimeout = null;
  wrapper.addEventListener("scroll", () => {
    if (scrollTimeout) clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      const center = wrapper.scrollLeft + wrapper.clientWidth / 2;
      let nearestIndex = 0;
      let nearestDist = Infinity;
      sections.forEach((sec, i) => {
        const secCenter = sec.offsetLeft + sec.offsetWidth / 2;
        const dist = Math.abs(secCenter - center);
        if (dist < nearestDist) {
          nearestDist = dist;
          nearestIndex = i;
        }
      });
      tabs.forEach(t => t.classList.remove("active"));
      if (tabs[nearestIndex]) tabs[nearestIndex].classList.add("active");
      const targetLeft = sections[nearestIndex].offsetLeft;
      wrapper.scrollTo({ left: targetLeft, behavior: "smooth" });
    }, 120);
  });

  // モーダル関連
  const modal = document.getElementById("stock-modal");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.querySelector(".modal-close");

  const escapeHTML = str => String(str).replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );

  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
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

    // キーボード操作で開ける
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

  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && modal.style.display === "block") closeModal();
  });

  // 横スクロール（証券会社エリアのみ）
  let isDragging = false;
  let startX = 0, startScrollLeft = 0;

  wrapper.addEventListener("mousedown", e => {
    isDragging = true;
    startX = e.pageX - wrapper.offsetLeft;
    startScrollLeft = wrapper.scrollLeft;
    wrapper.classList.add("dragging");
  });
  wrapper.addEventListener("mouseleave", () => { isDragging = false; wrapper.classList.remove("dragging"); });
  wrapper.addEventListener("mouseup", () => { isDragging = false; wrapper.classList.remove("dragging"); });
  wrapper.addEventListener("mousemove", e => {
    if (!isDragging) return;
    e.preventDefault();
    const x = e.pageX - wrapper.offsetLeft;
    wrapper.scrollLeft = startScrollLeft - (x - startX);
  });

  // タッチ横スクロール（証券会社エリアのみ）
  let touchStartX = 0, touchStartScroll = 0;
  wrapper.addEventListener("touchstart", e => {
    touchStartX = e.touches[0].pageX;
    touchStartScroll = wrapper.scrollLeft;
  });
  wrapper.addEventListener("touchmove", e => {
    const x = e.touches[0].pageX;
    wrapper.scrollLeft = touchStartScroll - (x - touchStartX);
  });

  // カード部分では横スワイプ禁止（縦スクロール優先）
  document.querySelectorAll(".broker-cards-wrapper").forEach(cardsWrapper => {
    cardsWrapper.addEventListener("touchmove", e => {
      const touch = e.touches[0];
      if (!touch) return;
      const deltaX = Math.abs(touch.pageX - touchStartX);
      const deltaY = Math.abs(touch.pageY - 0); // Y方向は制限なし
      if (deltaX > deltaY) {
        // 横方向の動きが大きい場合 → 横スクロール無効化
        e.stopPropagation();
      }
    }, { passive: false });
  });
});