/*
=========================================
[FILE] dashboard.js
[PATH] static/kakeibo/js/dashboard.js

このファイルは何？
- dashboard のバー表示を data-pct から設定する
- 追加：年度/年月 select を変えたら GET で自動送信
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

  function bindAutoSubmit(){
    document.querySelectorAll("select[data-autosubmit='1']").forEach(sel=>{
      sel.addEventListener("change", ()=>{
        const form = sel.closest("form");
        if (form) form.submit();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", ()=>{
    setBars();
    bindAutoSubmit();
  });
})();