/* 0.5刻みのスター描画
   要素: <span class="ai-stars" data-score100="67"></span>
   算出: score100(0..100) → stars(1.0..5.0, 0.5刻み)
*/
(function () {
  function toHalfStar(score100) {
    const s = Math.max(0, Math.min(100, Number(score100) || 0)) / 100; // 0..1
    const stars = 1 + 4 * s;                       // 0→1★, 1→5★
    return Math.round(stars * 2) / 2;              // 0.5刻み丸め
  }

  function mountStars(root) {
    const els = (root || document).querySelectorAll(".ai-stars");
    els.forEach(el => {
      // 既存データ：score_100 を読む（JSON変更不要）
      const s100 = el.getAttribute("data-score100");
      const stars = toHalfStar(s100);
      const pct = Math.max(0, Math.min(100, (stars / 5) * 100)); // 前景の幅%

      el.innerHTML = `
        <span class="stars-back">★★★★★</span>
        <span class="stars-front" style="width:${pct}%">★★★★★</span>
        <span class="stars-num">${stars.toFixed(1)}</span>
      `;
      el.setAttribute("aria-label", `${stars.toFixed(1)} stars`);
      el.setAttribute("role", "img");
    });
  }

  // 初期化
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mountStars());
  } else {
    mountStars();
  }

  // 必要なら外部から再描画を呼べるようにエクスポート
  window.AIStarsRender = mountStars;
})();