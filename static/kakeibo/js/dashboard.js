/*
=========================================
[FILE] dashboard.js
[PATH] static/kakeibo/js/dashboard.js

このファイルは何？
- dashboard のバー表示を data-pct から設定する
- A：UIは軽く、JSは最小限（バーだけ）
=========================================
*/

(function(){
  function clamp(n, min, max){
    n = Number(n);
    if (Number.isNaN(n)) return min;
    return Math.max(min, Math.min(max, n));
  }

  function setBars(){
    document.querySelectorAll("[data-bar][data-pct]").forEach(el=>{
      const pct = clamp(el.getAttribute("data-pct"), 0, 100);
      el.style.width = pct.toFixed(0) + "%";
    });
  }

  document.addEventListener("DOMContentLoaded", setBars);
})();