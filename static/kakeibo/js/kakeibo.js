/*
[FILE] kakeibo.js
[PATH] <project_root>/static/kakeibo/js/kakeibo.js

このファイルは何？
- 家計簿アプリ専用のJavaScript。
- いまは「設定の削除confirm」など、軽いUI補助のみ。

✅ 追加（共通UI品質）
- iOS Safari のピンチズーム / ジェスチャーズームを抑止
- ダブルタップズームを抑止（アプリ風固定UIのため）
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

  // =========================================================
  // ✅ Zoom禁止（iOS Safari対策）
  // - ピンチ（gesture）系を抑止
  // - ダブルタップズームも抑止
  // =========================================================

  // iOSのジェスチャー（ピンチ）を抑止
  ["gesturestart", "gesturechange", "gestureend"].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      e.preventDefault();
    }, { passive: false });
  });

  // ダブルタップズーム抑止（iOS Safari）
  let lastTouchEnd = 0;
  document.addEventListener("touchend", (e) => {
    const now = Date.now();
    if (now - lastTouchEnd <= 300) {
      e.preventDefault();
    }
    lastTouchEnd = now;
  }, { passive: false });
});