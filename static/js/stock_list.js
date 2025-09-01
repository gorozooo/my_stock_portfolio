document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".broker-tab");
  const sectionsWrapper = document.querySelector(".broker-vertical-wrapper");
  const sections = document.querySelectorAll(".broker-section");

  // 初期表示：最初の証券会社
  tabs[0].classList.add("active");
  sectionsWrapper.scrollLeft = 0;

  // タブクリックでスクロール
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      const index = tab.dataset.brokerIndex;
      const target = sections[index];
      target.scrollIntoView({ behavior: "smooth", inline: "start" });
    });
  });

  // 株カードクリック
  document.querySelectorAll(".stock-card").forEach(card => {
    card.addEventListener("click", () => {
      const name = card.dataset.name;
      const ticker = card.dataset.ticker;
      const shares = card.dataset.shares;
      const unitPrice = card.dataset.unit_price;
      const currentPrice = card.dataset.current_price;
      const profit = card.dataset.profit;
      const profitRate = card.dataset.profit_rate;
      alert(`${name} (${ticker})\n株数: ${shares}\n取得単価: ¥${unitPrice}\n現在株価: ¥${currentPrice}\n損益: ¥${profit} (${profitRate}%)`);
    });
  });

  // 横スクロールカード操作（マウス＆タッチ）
  document.querySelectorAll(".broker-cards-wrapper").forEach(wrapper => {
    let isDown = false, startX, scrollLeft;
    wrapper.addEventListener("mousedown", e => {
      isDown = true;
      startX = e.pageX - wrapper.offsetLeft;
      scrollLeft = wrapper.scrollLeft;
    });
    wrapper.addEventListener("mouseleave", () => isDown = false);
    wrapper.addEventListener("mouseup", () => isDown = false);
    wrapper.addEventListener("mousemove", e => {
      if(!isDown) return;
      e.preventDefault();
      const x = e.pageX - wrapper.offsetLeft;
      wrapper.scrollLeft = scrollLeft - (x - startX);
    });

    // タッチ対応
    let startTouchX = 0, startScroll = 0;
    wrapper.addEventListener("touchstart", e => {
      startTouchX = e.touches[0].pageX;
      startScroll = wrapper.scrollLeft;
    });
    wrapper.addEventListener("touchmove", e => {
      const touchX = e.touches[0].pageX;
      wrapper.scrollLeft = startScroll - (touchX - startTouchX);
    });
  });

  // 縦スクロールに応じてタブ自動切替
  sectionsWrapper.addEventListener("scroll", () => {
    const wrapperLeft = sectionsWrapper.scrollLeft;
    let closestIndex = 0;
    let minDistance = Infinity;
    sections.forEach((section, index) => {
      const offset = section.offsetLeft;
      const distance = Math.abs(wrapperLeft - offset);
      if(distance < minDistance){
        minDistance = distance;
        closestIndex = index;
      }
    });
    tabs.forEach(t => t.classList.remove("active"));
    tabs[closestIndex].classList.add("active");
  });
});