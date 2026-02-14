/*
=========================================
[FILE] kakeibo_expense.js
[PATH] static/kakeibo/js/kakeibo_expense.js

このファイルは何？
- 変動費フォームで
  1) 種類が CARD のときだけ「カード選択」を表示
  2) owner（誰の支出）に合わせてカード候補を絞る（BならBのカードだけ）
- API: /kakeibo/api/cards/?owner=B
=========================================
*/

(function () {
  function qs(sel) { return document.querySelector(sel); }

  async function fetchItems(url) {
    const res = await fetch(url, { credentials: "same-origin" });
    const data = await res.json();
    if (!data.ok) return [];
    return data.items || [];
  }

  function replaceOptions(selectEl, items, placeholderText) {
    if (!selectEl) return;

    const current = selectEl.value;
    selectEl.innerHTML = "";

    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = placeholderText || "---------";
    selectEl.appendChild(ph);

    for (const it of items) {
      const opt = document.createElement("option");
      opt.value = String(it.id);
      opt.textContent = it.name;
      selectEl.appendChild(opt);
    }

    // 可能なら元の選択を復元
    if (current) {
      const found = Array.from(selectEl.options).some(o => o.value === current);
      if (found) selectEl.value = current;
    }
  }

  function setRowVisible(fieldId, visible) {
    const el = qs(fieldId);
    if (!el) return;
    // Django form.as_p は <p> に入ることが多いので親を探す
    const p = el.closest("p") || el.parentElement;
    if (!p) return;
    p.style.display = visible ? "" : "none";
  }

  async function setupVariableExpense() {
    const ownerEl = qs("#id_owner");
    const varTypeEl = qs("#id_var_type");
    const cardEl = qs("#id_card");

    if (!ownerEl || !varTypeEl || !cardEl) return;

    async function refreshCards() {
      const owner = ownerEl.value;
      if (!owner) {
        replaceOptions(cardEl, [], "先に「誰の支出」を選択");
        return;
      }
      const items = await fetchItems(`/kakeibo/api/cards/?owner=${encodeURIComponent(owner)}`);
      replaceOptions(cardEl, items, "カードを選択");
    }

    function refreshVisibility() {
      const vt = varTypeEl.value;
      const isCard = (vt === "CARD");
      setRowVisible("#id_card", isCard);
      // カード以外なら選択を消す
      if (!isCard) cardEl.value = "";
    }

    ownerEl.addEventListener("change", async () => {
      await refreshCards();
    });

    varTypeEl.addEventListener("change", () => {
      refreshVisibility();
    });

    // 初期表示
    await refreshCards();
    refreshVisibility();
  }

  document.addEventListener("DOMContentLoaded", setupVariableExpense);
})();