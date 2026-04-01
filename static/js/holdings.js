/* [FILE] holdings.js
   [PATH] static/js/holdings.js

   このファイルは何？
   - /holdings/ 専用の軽量JS
   - KPI / フィルター / スパーク / partial 差し替えを廃止したため、
     役割は「details の開閉補助」だけに絞る

   今回の方針
   - 一度に1行だけ開く
   - 外側をタップしたら開いている行を閉じる
*/

(() => {
  function bindHoldingRows() {
    const rows = Array.from(document.querySelectorAll(".holding-row"));
    if (!rows.length) return;

    rows.forEach((row) => {
      if (row.dataset.bound === "1") return;
      row.dataset.bound = "1";

      row.addEventListener("toggle", () => {
        if (!row.open) return;
        rows.forEach((other) => {
          if (other !== row) other.open = false;
        });
      });
    });

    document.addEventListener("click", (e) => {
      if (e.target.closest(".holding-row")) return;
      rows.forEach((row) => {
        row.open = false;
      });
    });
  }

  document.addEventListener("DOMContentLoaded", bindHoldingRows);
})();