/*
[FILE] kakeibo_toast.js
[PATH] <project_root>/static/kakeibo/js/kakeibo_toast.js

このファイルは何？
- base_kakeibo.html に描画された .kb-toast を自動で表示→数秒で消す。
- タップで即閉じる。
- ★今回：
  - 表示時間を 5秒 にする
  - 文字を読みやすく整形（" / " を改行に、"：" の後も改行）
*/

(function () {
  function closeToast(el) {
    if (!el) return;
    el.classList.remove("is-show");
    window.setTimeout(() => {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  function prettifyMessage(s) {
    if (!s) return s;

    // "タイトル： ..." を "タイトル：\n..." に
    s = s.replace("： ", "：\n");

    // " / " 区切りを改行へ
    s = s.replace(/\s*\/\s*/g, "\n");

    // 連続改行は1つに
    s = s.replace(/\n{3,}/g, "\n\n");

    return s.trim();
  }

  function boot() {
    const toasts = Array.from(document.querySelectorAll(".kb-toast"));
    if (!toasts.length) return;

    toasts.forEach((t, idx) => {
      const x = t.querySelector(".kb-toast-x");
      if (x) x.addEventListener("click", (e) => {
        e.stopPropagation();
        closeToast(t);
      });

      // toast本体タップでも閉じる
      t.addEventListener("click", () => closeToast(t));

      // ✅ 見やすく整形（改行表示はCSS側で white-space:pre-wrap）
      const msg = t.querySelector(".kb-toast-msg");
      if (msg) {
        msg.textContent = prettifyMessage(msg.textContent);
      }

      window.setTimeout(() => t.classList.add("is-show"), 50 + idx * 60);

      // ✅ 5秒表示（複数あるときは少しだけズラす）
      const ttl = 5000 + idx * 250;
      window.setTimeout(() => closeToast(t), ttl);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();