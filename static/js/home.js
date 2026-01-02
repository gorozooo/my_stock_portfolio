// static/js/home.js
(function(){
  const rail = document.getElementById("rail");
  const dots = document.getElementById("dots");
  if (!rail || !dots) return;

  const decks = Array.from(rail.querySelectorAll(".deck"));
  if (!decks || decks.length === 0) return;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // dots build
  const buildDots = () => {
    dots.innerHTML = decks.map((_,i)=>`<span class="dot ${i===0?'on':''}" data-i="${i}"></span>`).join("");
    return Array.from(dots.querySelectorAll(".dot"));
  };

  let dotEls = buildDots();
  if (!dotEls || dotEls.length === 0) return;

  const setOn = (idx) => {
    dotEls.forEach((d,i)=>d.classList.toggle("on", i===idx));
  };

  const getDeckWidth = () => {
    // rail の可視幅 = 1枚の幅（flex:0 0 100% 前提）
    return rail.clientWidth || 1;
  };

  const onScroll = () => {
    const x = rail.scrollLeft || 0;
    const w = getDeckWidth();
    const idx = Math.round(x / w);
    setOn(clamp(idx, 0, decks.length - 1));
  };

  // scroll -> requestAnimationFrame
  rail.addEventListener("scroll", () => {
    window.requestAnimationFrame(onScroll);
  }, { passive: true });

  // dot click -> scrollTo
  const bindDotClicks = () => {
    dotEls.forEach(el=>{
      el.addEventListener("click", ()=>{
        const i = parseInt(el.getAttribute("data-i") || "0", 10);
        const w = getDeckWidth();
        rail.scrollTo({ left: i * w, behavior: "smooth" });
      });
    });
  };
  bindDotClicks();

  // resize/orientation -> keep current index and rebuild dots (保険)
  let resizeTimer = null;
  const onResize = () => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      const x = rail.scrollLeft || 0;
      const w = getDeckWidth();
      const idx = clamp(Math.round(x / w), 0, decks.length - 1);

      // dots rebuild
      dotEls = buildDots();
      bindDotClicks();
      setOn(idx);

      // snap to idx
      rail.scrollTo({ left: idx * w, behavior: "auto" });
    }, 120);
  };

  window.addEventListener("resize", onResize, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", onResize, { passive: true });
  }

  // init
  window.requestAnimationFrame(onScroll);

  // ticker: 二重化で途切れ対策（テキストが短い時だけ）
  const tickerText = document.getElementById("tickerText");
  if (tickerText) {
    const raw = (tickerText.textContent || "").trim();
    // “NEWS: 準備中” みたいに短い場合も、二重化して流れを作る
    if (raw.length > 0) {
      const sep = "  ／  ";
      const doubled = raw + sep + raw + sep + raw;
      tickerText.textContent = doubled;
    }
  }
})();