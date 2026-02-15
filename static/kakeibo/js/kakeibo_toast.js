/*
[FILE] kakeibo_toast.js
[PATH] <project_root>/static/kakeibo/js/kakeibo_toast.js

このファイルは何？
- base_kakeibo.html に描画された .kb-toast を自動で表示→数秒で消す。
- タップで即閉じる。
*/

(function () {
  function closeToast(el) {
    if (!el) return;
    el.classList.remove("is-show");
    window.setTimeout(() => {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  function boot() {
    const toasts = Array.from(document.querySelectorAll(".kb-toast"));
    if (!toasts.length) return;

    // 連続表示でも自然に見えるように少しずつ出す
    toasts.forEach((t, idx) => {
      // close button
      const x = t.querySelector(".kb-toast-x");
      if (x) x.addEventListener("click", () => closeToast(t));

      // toast本体タップでも閉じる（誤タップでもストレス減）
      t.addEventListener("click", () => closeToast(t));

      window.setTimeout(() => t.classList.add("is-show"), 50 + idx * 60);

      // 自動で消える（長すぎると邪魔、短すぎると読めない…の妥協点）
      const ttl = 2600 + idx * 300;
      window.setTimeout(() => closeToast(t), ttl);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();