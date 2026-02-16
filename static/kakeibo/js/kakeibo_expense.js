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
    const p = el.closest("p") || el.parentElement;
    if (!p) return;
    p.style.display = visible ? "" : "none";
  }

  function readOwnerValue(ownerEl) {
    if (!ownerEl) return "";
    let v = (ownerEl.value || "").trim();

    // iPhoneで「初期描画直後に value が空扱い」になるケースを潰す
    if (!v && ownerEl.selectedIndex >= 0) {
      const opt = ownerEl.options[ownerEl.selectedIndex];
      const ov = opt ? String(opt.value || "").trim() : "";
      if (ov) v = ov;
    }
    return v;
  }

  async function setupVariableExpense() {
    const ownerEl = qs("#id_owner");
    const varTypeEl = qs("#id_var_type");
    const cardEl = qs("#id_card");

    if (!ownerEl || !varTypeEl || !cardEl) return;

    async function refreshCards() {
      const owner = readOwnerValue(ownerEl);

      if (!owner) {
        replaceOptions(cardEl, [], "先に「誰の支出」を選択");
        return false;
      }

      const items = await fetchItems(`/kakeibo/api/cards/?owner=${encodeURIComponent(owner)}`);
      replaceOptions(cardEl, items, (items.length ? "カードを選択" : "この所有者のカードがありません"));
      return true;
    }

    function refreshVisibility() {
      const vt = (varTypeEl.value || "").trim();
      const isCard = (vt === "CARD");
      setRowVisible("#id_card", isCard);

      // カード以外なら選択を消す
      if (!isCard) cardEl.value = "";
    }

    // ownerは iPhone で change が遅れることがあるので input/blur も拾う
    ownerEl.addEventListener("change", refreshCards);
    ownerEl.addEventListener("input", refreshCards);
    ownerEl.addEventListener("blur", refreshCards);

    varTypeEl.addEventListener("change", () => {
      refreshVisibility();
      // 種類をカードにした瞬間にカード候補を確実に出す
      if ((varTypeEl.value || "").trim() === "CARD") {
        refreshCards();
      }
    });

    // 初期表示：即1回 + 少し遅延でもう1回（Safari対策）
    refreshVisibility();
    await refreshCards();
    window.setTimeout(refreshCards, 250);
    window.setTimeout(refreshCards, 800);
  }

  document.addEventListener("DOMContentLoaded", setupVariableExpense);
})();