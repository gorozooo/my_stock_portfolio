// 
// [FILE] static/autotrade/tuning.js
// [PATH] <project_root>/static/autotrade/tuning.js
//
// このファイルは何？
// - 実験室の「検証」「アーカイブ」などの危険操作に confirm を付けるだけの軽JSです。
// - 入力制限はしない（警告は将来）。
//

(() => {
  const binds = document.querySelectorAll("[data-confirm]");
  binds.forEach((el) => {
    el.addEventListener("click", (e) => {
      const msg = el.getAttribute("data-confirm");
      if (!msg) return;
      const ok = confirm(msg);
      if (!ok) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });
})();