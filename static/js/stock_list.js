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

  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
      const name = card.dataset.name;
      const ticker = card.dataset.ticker;
      const shares = card.dataset.shares;
      const unitPrice = card.dataset.unit_price;
      const currentPrice = card.dataset.current_price;
      const profit = card.dataset.profit;
      const profitRate = card.dataset.profit_rate;

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

  // 横スクロール（マウスドラッグ＆タッチ対応）
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
});