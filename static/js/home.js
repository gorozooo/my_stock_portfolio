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
    return rail.clientWidth || 1;
  };

  const onScroll = () => {
    const x = rail.scrollLeft || 0;
    const w = getDeckWidth();
    const idx = Math.round(x / w);
    setOn(clamp(idx, 0, decks.length - 1));
  };

  rail.addEventListener("scroll", () => {
    window.requestAnimationFrame(onScroll);
  }, { passive: true });

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

  let resizeTimer = null;
  const onResize = () => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      const x = rail.scrollLeft || 0;
      const w = getDeckWidth();
      const idx = clamp(Math.round(x / w), 0, decks.length - 1);

      dotEls = buildDots();
      bindDotClicks();
      setOn(idx);

      rail.scrollTo({ left: idx * w, behavior: "auto" });

      // tickerも再計算（向き変更で幅が変わる）
      setupTicker();
    }, 120);
  };

  window.addEventListener("resize", onResize, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", onResize, { passive: true });
  }

  window.requestAnimationFrame(onScroll);

  // =========================
  // ticker: 読める速度に自動調整
  // =========================
  const tickerText = document.getElementById("tickerText");

  const setupTicker = () => {
    if (!tickerText) return;

    const raw = (tickerText.getAttribute("data-raw") || tickerText.textContent || "").trim();
    if (!raw) return;

    // まず raw を保存（何回も二重化しない）
    tickerText.setAttribute("data-raw", raw);

    const track = tickerText.closest(".ticker-track");
    const trackW = (track && track.clientWidth) ? track.clientWidth : 300;

    // テキストが短い時は“流さない”（読ませる）
    // 目安: track幅の1.2倍以下なら固定表示
    tickerText.classList.remove("is-marquee");
    tickerText.style.setProperty("--marqueeDur", "0s");
    tickerText.style.setProperty("--marqueeDist", "0px");
    tickerText.textContent = raw;

    // 一旦DOM反映後に幅を測る
    window.requestAnimationFrame(() => {
      const textW = tickerText.scrollWidth || 0;

      if (textW <= trackW * 1.2) {
        // 固定表示でOK
        tickerText.classList.remove("is-marquee");
        tickerText.textContent = raw;
        return;
      }

      // 長い時だけ “ほどほどに” ループできるように二重化（3回まで）
      const sep = "   ／   ";
      const doubled = raw + sep + raw;
      tickerText.textContent = doubled;

      // 再測定
      window.requestAnimationFrame(() => {
        const newW = tickerText.scrollWidth || textW;

        // 距離: “raw 1回分 + 余白” くらい動けばOK
        // newW 全部動かすと長すぎるので、動かす距離を制御
        const dist = Math.max(textW + 80, trackW + 120);

        // 速度(px/sec)：読みやすさ最優先で遅め
        // 目安 28〜40 px/sec（遅いほど読みやすい）
        const pxPerSec = 32;

        // 最短/最長（極端対策）
        let dur = dist / pxPerSec;
        dur = Math.max(18, Math.min(48, dur)); // 18〜48秒

        tickerText.style.setProperty("--marqueeDist", `${dist}px`);
        tickerText.style.setProperty("--marqueeDur", `${dur}s`);
        tickerText.classList.add("is-marquee");
      });
    });
  };

  setupTicker();
})();