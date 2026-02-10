// 
// [FILE] static/autotrade/tuning.js
// [PATH] <project_root>/static/autotrade/tuning.js
//
// このファイルは何？
// - 実験室の「検証」「アーカイブ」「候補保存」「ACTIVE昇格」「ロールバック」「再検証」など、
//   危険/誤操作しやすい操作に confirm を付けるための軽JSです。
// - 入力制限はしない（警告は将来）。
// - data-confirm 属性が付いた要素すべてに適用します。
// - <button> でも <a> でも動きます（フォームsubmitの誤爆も止めます）。
//

(() => {
  const binds = document.querySelectorAll("[data-confirm]");
  if (!binds || binds.length === 0) return;

  binds.forEach((el) => {
    // 既にバインド済みを避ける（同一テンプレで複数読み込み・HTMX等でも安全）
    if (el.__autotrade_confirm_bound) return;
    el.__autotrade_confirm_bound = true;

    el.addEventListener("click", (e) => {
      const msg = el.getAttribute("data-confirm");
      if (!msg) return;

      const ok = confirm(msg);
      if (!ok) {
        // submit / navigation / bubbling を全部止める
        e.preventDefault();
        e.stopPropagation();

        // もし form submit の途中でも止める（安全）
        if (typeof e.stopImmediatePropagation === "function") {
          e.stopImmediatePropagation();
        }
      }
    }, { capture: true }); // captureで先に止める（フォームsubmitより前）
  });
})();