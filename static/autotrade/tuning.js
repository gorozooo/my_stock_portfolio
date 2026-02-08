// [FILE] static/autotrade/tuning.js
// [PATH] <project_root>/static/autotrade/tuning.js
// このファイルは何？
// - 実験室（TuningProfile）画面の軽いUI補助。
// - 今は「アーカイブの確認」だけ。

(() => {
  document.querySelectorAll("[data-confirm]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const msg = btn.getAttribute("data-confirm");
      if (msg && !confirm(msg)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });
})();