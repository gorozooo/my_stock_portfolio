/*
[FILE] kakeibo_expense.js
[PATH] <project_root>/static/kakeibo/js/kakeibo_expense.js

このファイルは何？
- 変動費フォームで「カード」欄を出し分ける。
- var_type=CARD のときだけ card 欄を表示。
*/

(function () {
  function findWrapper(el) {
    // Djangoの form.as_p は <p> で囲まれるので、基本これで取れる
    if (!el) return null;
    return el.closest("p") || el.parentElement;
  }

  function toggleCardField() {
    var varType = document.getElementById("id_var_type");
    var card = document.getElementById("id_card");
    var wrap = findWrapper(card);

    if (!varType || !card || !wrap) return;

    if (varType.value === "CARD") {
      wrap.style.display = "";
    } else {
      // 立替(ADVANCE)などは非表示＆値もクリア
      wrap.style.display = "none";
      card.value = "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var varType = document.getElementById("id_var_type");
    if (!varType) return;

    toggleCardField();
    varType.addEventListener("change", toggleCardField);
  });
})();