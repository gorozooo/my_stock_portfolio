/*
[FILE] kakeibo.js
[PATH] <project_root>/static/kakeibo/js/kakeibo.js

このファイルは何？
- 家計簿アプリ専用のJavaScript。
- いまは「設定の削除confirm」など、軽いUI補助のみ。
*/

document.addEventListener("DOMContentLoaded", () => {
  // 本番で気にしなくてOK。動作確認用。
  console.log("[kakeibo] loaded");

  // 設定画面：削除ボタンの confirm
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const msg = form.getAttribute("data-confirm") || "削除しますか？";
      if (!confirm(msg)) {
        e.preventDefault();
      }
    });
  });
});