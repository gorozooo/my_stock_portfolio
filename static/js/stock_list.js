/* スマホファースト設計、HTML/CSS/JS分けて設計 */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".broker-tab");
  const wrapper = document.querySelector(".broker-horizontal-wrapper");
  const sections = document.querySelectorAll(".broker-section");

  // 初期表示：最初の証券会社を表示
  if (tabs.length > 0) tabs[0].classList.add("active");
  sections.forEach((s, i) => s.style.display = i === 0 ? "flex" : "none");

  // タブクリックで横スクロール切替
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const index = parseInt(tab.dataset.brokerIndex);

      // タブのアクティブ切替
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      // 証券会社セクションの表示切替
      sections.forEach((s, i) => s.style.display = i === index ? "flex" : "none");

      // 横スクロールでスムーズ移動
      sections[index].scrollIntoView({ behavior: "smooth", inline: "start" });
    });
  });

  // 株カードクリックでモーダル表示
  const modal = document.getElementById("stock-modal");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.querySelector(".modal-close");

  // HTMLエスケープ用ユーティリティ
  const escapeHTML = str =>
    String(str).replace(/[&<>"']/g, m => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]
    ));

  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
      const name = escapeHTML(card.dataset.name);
      const ticker = escapeHTML(card.dataset.ticker);
      const shares = escapeHTML(card.dataset.shares);
      const unitPrice = escapeHTML(card.dataset.unit_price);
      const currentPrice = escapeHTML(card.dataset.current_price);
      const profit = escapeHTML(card.dataset.profit);
      const profitRate = escapeHTML(card.dataset.profit_rate);

      // モーダル内容更新
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

  // モーダル閉じる
  modalClose.addEventListener("click", () => { modal.style.display = "none"; });
  modal.addEventListener("click", e => { if(e.target === modal) modal.style.display = "none"; });

  // 🔹 追加: ESC キーでモーダルを閉じる
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && modal.style.display === "block") {
      modal.style.display = "none";
    }
  });

  // 縦スクロール（株カードリスト：マウスドラッグ＆タッチ対応）
  wrapper.querySelectorAll(".broker-section").forEach(section => {
    const cardsWrapper = section.querySelector(".broker-cards-wrapper");
    if (!cardsWrapper) return;

    let isDown = false;
    let startY, scrollTop;

    // マウス操作
    cardsWrapper.addEventListener("mousedown", e => {
      isDown = true;
      startY = e.pageY - cardsWrapper.offsetTop;
      scrollTop = cardsWrapper.scrollTop;
    });
    cardsWrapper.addEventListener("mouseleave", () => isDown = false);
    cardsWrapper.addEventListener("mouseup", () => isDown = false);
    cardsWrapper.addEventListener("mousemove", e => {
      if(!isDown) return;
      e.preventDefault();
      const y = e.pageY - cardsWrapper.offsetTop;
      cardsWrapper.scrollTop = scrollTop - (y - startY);
    });

    // タッチ操作
    let startTouchY = 0, startScroll = 0;
    cardsWrapper.addEventListener("touchstart", e => {
      startTouchY = e.touches[0].pageY;
      startScroll = cardsWrapper.scrollTop;
    });
    cardsWrapper.addEventListener("touchmove", e => {
      const touchY = e.touches[0].pageY;
      cardsWrapper.scrollTop = startScroll - (touchY - startTouchY);
    });
  });

  // 横スクロール（broker-horizontal-wrapper：マウスドラッグ＆タッチ対応）
  let isDragging = false;
  let startX, scrollLeft;

  wrapper.addEventListener("mousedown", e => {
    isDragging = true;
    startX = e.pageX - wrapper.offsetLeft;
    scrollLeft = wrapper.scrollLeft;
    wrapper.classList.add("dragging");
  });
  wrapper.addEventListener("mouseleave", () => isDragging = false);
  wrapper.addEventListener("mouseup", () => {
    isDragging = false;
    wrapper.classList.remove("dragging");

    // 🔹 追加: スナップ（スクロールが一番近い section に揃う）
    const sectionWidth = sections[0].offsetWidth;
    const index = Math.round(wrapper.scrollLeft / sectionWidth);
    wrapper.scrollTo({ left: index * sectionWidth, behavior: "smooth" });
  });
  wrapper.addEventListener("mousemove", e => {
    if(!isDragging) return;
    e.preventDefault();
    const x = e.pageX - wrapper.offsetLeft;
    wrapper.scrollLeft = scrollLeft - (x - startX);
  });

  // タッチ操作
  let startTouchX = 0, startScrollX = 0;
  wrapper.addEventListener("touchstart", e => {
    startTouchX = e.touches[0].pageX;
    startScrollX = wrapper.scrollLeft;
  });
  wrapper.addEventListener("touchend", () => {
    // 🔹 追加: スナップ（タッチでもピタッと止まる）
    const sectionWidth = sections[0].offsetWidth;
    const index = Math.round(wrapper.scrollLeft / sectionWidth);
    wrapper.scrollTo({ left: index * sectionWidth, behavior: "smooth" });
  });
  wrapper.addEventListener("touchmove", e => {
    const touchX = e.touches[0].pageX;
    wrapper.scrollLeft = startScrollX - (touchX - startTouchX);
  });
});