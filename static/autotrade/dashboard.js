/*
[FILE] static/autotrade/dashboard.js
[PATH] <project_root>/static/autotrade/dashboard.js

このファイルは何？
- iPhone 1画面ダッシュボードの「動き（JS）」だけを管理します。
- 現段階では、将来の非常停止ボタンのための土台だけ置きます。

初心者ポイント：
- 画面の機能が増えても、ここに追加していけばHTMLは汚れません。
*/

(() => {
  const btn = document.getElementById("btnStop");
  if (!btn) return;

  // 次フェーズでAPIを作ったら、ここでPOSTして停止させる
  btn.addEventListener("click", () => {
    alert("非常停止は次フェーズで実装します（DBに停止フラグ→ジョブが参照）");
  });
})();