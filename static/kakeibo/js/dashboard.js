/*
=========================================
[FILE] dashboard.js
[PATH] static/kakeibo/js/dashboard.js

このファイルは何？
- dashboard のバー表示を data-pct から設定する
- C案：バーの発光スキャン演出を少しだけ追加（軽量）
=========================================
*/

(function(){
  function clamp(n, min, max){
    n = Number(n);
    if (Number.isNaN(n)) return min;
    return Math.max(min, Math.min(max, n));
  }

  function setBars(){
    const bars = document.querySelectorAll("[data-bar][data-pct]");
    bars.forEach(el=>{
      const pct = clamp(el.getAttribute("data-pct"), 0, 100);
      el.style.width = pct.toFixed(0) + "%";
    });

    // スキャン光を一回走らせる（重くならない範囲）
    document.querySelectorAll(".kb-bar-glow").forEach(wrap=>{
      // 既にアニメ中なら何もしない
      if (wrap.dataset.scanRunning === "1") return;
      wrap.dataset.scanRunning = "1";

      // 擬似要素の見た目だけ走らせるため、クラスでトリガ
      wrap.classList.add("kb-scan-on");

      // CSSだけで走らせたいが、既存CSSを増やしすぎないためJSで軽く制御
      // 1.1秒後に解除
      setTimeout(()=>{
        wrap.classList.remove("kb-scan-on");
        wrap.dataset.scanRunning = "0";
      }, 1100);
    });
  }

  function injectScanCSSOnce(){
    // kb-scan-on の時だけ kb-bar-glow::after を動かす
    if (document.getElementById("kb-scan-style")) return;
    const css = `
      .kb-scan-on.kb-bar-glow::after{
        animation: kbScan 1.1s ease forwards;
      }
      @keyframes kbScan{
        0%{ left:-30%; opacity:.0; }
        15%{ opacity:.55; }
        100%{ left:110%; opacity:0; }
      }
    `;
    const style = document.createElement("style");
    style.id = "kb-scan-style";
    style.textContent = css;
    document.head.appendChild(style);
  }

  document.addEventListener("DOMContentLoaded", function(){
    injectScanCSSOnce();
    setBars();
  });
})();