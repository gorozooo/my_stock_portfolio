/* [FILE] holdings.js
   [PATH] static/js/holdings.js

   このファイルは何？
   - /holdings/ 専用の軽量JS
   - 詳細ボタンでボトムシートを開く
   - 一覧内で縦に展開させず、補足情報だけを下から表示する
*/

(() => {
  function initHoldingDetailSheet() {
    const sheet = document.getElementById("holdingDetailSheet");
    const body = document.getElementById("detailSheetBody");
    const nameEl = document.getElementById("detailSheetName");
    const codeEl = document.getElementById("detailSheetCode");

    if (!sheet || !body || !nameEl || !codeEl) return;

    const closeSheet = () => {
      sheet.classList.remove("is-open");
      sheet.setAttribute("aria-hidden", "true");
      body.innerHTML = "";
      codeEl.textContent = "";
      document.body.style.overflow = "";
    };

    const openSheet = (btn) => {
      const targetId = btn.dataset.detailTarget || "";
      const target = document.getElementById(targetId);
      if (!target) return;

      nameEl.textContent = btn.dataset.name || "詳細";
      codeEl.textContent = btn.dataset.code || "";
      body.innerHTML = target.innerHTML;

      sheet.classList.add("is-open");
      sheet.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
    };

    document.querySelectorAll("[data-open-detail]").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";

      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openSheet(btn);
      });
    });

    sheet.querySelectorAll("[data-close-detail]").forEach((el) => {
      el.addEventListener("click", closeSheet);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && sheet.classList.contains("is-open")) {
        closeSheet();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", initHoldingDetailSheet);
})();