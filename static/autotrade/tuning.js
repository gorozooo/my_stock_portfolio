// 
// [FILE] static/autotrade/tuning.js
// [PATH] <project_root>/static/autotrade/tuning.js
//
// このファイルは何？
// - 実験室（TuningProfile / 検証結果）画面専用の軽量JS。
// - iPhone前提：
//   ・危険操作（検証 / CANDIDATE / ACTIVE / ロールバック）に confirm を付与
//   ・「理由（日本語＋数字＋円）」は折りたたみで初期非表示
//   ・同一チューニングでの再検証を“戻らず”に行えるUXを補助
//
// ポリシー：
// - 状態はサーバーが真実（JSは表示制御のみ）
// - 失敗しても壊れない
//

(() => {

  /* =========================
     confirm 付きボタン
     ========================= */
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

  /* =========================
     理由（details/summary）自動補助
     ========================= */
  const detailsList = document.querySelectorAll("details.lab-details");
  detailsList.forEach((d) => {
    const summary = d.querySelector("summary");
    if (!summary) return;

    // iPhoneで誤タップしにくいよう余白を調整
    summary.addEventListener("click", () => {
      // 開閉はブラウザ標準に任せる（ここでは副作用なし）
    });
  });

  /* =========================
     再検証ボタン（同一ページ）
     ========================= */
  const rerunBtns = document.querySelectorAll("[data-rerun]");
  rerunBtns.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const msg = btn.getAttribute("data-confirm");
      if (msg) {
        const ok = confirm(msg);
        if (!ok) {
          e.preventDefault();
          return;
        }
      }
      // 通常のPOST送信に任せる（JSで何もしない）
    });
  });

})();