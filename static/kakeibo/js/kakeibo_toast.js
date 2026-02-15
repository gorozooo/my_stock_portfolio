/*
[FILE] kakeibo_toast.js
[PATH] <project_root>/static/kakeibo/js/kakeibo_toast.js

このファイルは何？
- base_kakeibo.html に描画された .kb-toast を自動で表示→一定時間で消す。
- タップで即閉じる。
- ★今回：上表示に合わせて自然なアニメ、表示時間を5秒、文字を3行に整形。
*/

(function () {
  const TTL_MS = 5000; // ✅ 表示時間：5秒

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function closeToast(el) {
    if (!el) return;
    el.classList.remove("is-show");
    window.setTimeout(() => {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  function formatMessage(raw) {
    // 例： "✅ 収入を更新 : 2026-01 / G（妻） / 給与 / ¥100,000"
    const s = (raw || "").trim();

    // ":" があるなら、左=タイトル / 右=詳細
    const idx = s.indexOf(":");
    let head = s;
    let tail = "";

    if (idx !== -1) {
      head = s.slice(0, idx).trim();
      tail = s.slice(idx + 1).trim();
    }

    // "/" 区切りを分解
    const parts = tail
      ? tail.split("/").map(x => x.trim()).filter(Boolean)
      : [];

    // 金額っぽい要素を最後から探す
    let amount = "";
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      if (p.includes("¥") || p.match(/^\d{1,3}(,\d{3})*$/) || p.match(/^\d+$/)) {
        amount = p;
        parts.splice(i, 1);
        break;
      }
    }

    const line1 = escapeHtml(head || "完了");
    const line2 = escapeHtml(parts.join(" / "));
    const line3 = escapeHtml(amount);

    // 空行は出さない
    let html = `<div class="kb-toast-line">${line1}</div>`;
    if (line2) html += `<div class="kb-toast-sub">${line2}</div>`;
    if (line3) html += `<div class="kb-toast-amt">${line3}</div>`;

    return html;
  }

  function boot() {
    const toasts = Array.from(document.querySelectorAll(".kb-toast"));
    if (!toasts.length) return;

    toasts.forEach((t, idx) => {
      // ✅ 文言を整形（安全に）
      const msg = t.querySelector(".kb-toast-msg");
      if (msg) {
        const raw = msg.textContent || "";
        msg.innerHTML = formatMessage(raw);
      }

      // close button
      const x = t.querySelector(".kb-toast-x");
      if (x) x.addEventListener("click", (e) => { e.stopPropagation(); closeToast(t); });

      // toast本体タップでも閉じる
      t.addEventListener("click", () => closeToast(t));

      // 連続表示でも自然に見えるように少しずつ出す
      window.setTimeout(() => t.classList.add("is-show"), 50 + idx * 60);

      // ✅ 5秒で消える（全トースト共通）
      window.setTimeout(() => closeToast(t), TTL_MS);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();