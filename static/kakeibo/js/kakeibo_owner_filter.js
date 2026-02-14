/*
=========================================
[FILE] kakeibo_owner_filter.js
[PATH] static/kakeibo/js/kakeibo_owner_filter.js

このファイルは何？
- owner（HOUSE/B/G）を選んだら、口座候補をAPIから取って入れ替える。
- 銀行残高ページ（bank_balance.html）で使う。
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

  async function setupBankOwnerFilter() {
    // Djangoの自動生成ID（ModelFormのフィールド名）
    const ownerEl = qs("#id_owner");
    const accountEl = qs("#id_account");
    if (!ownerEl || !accountEl) return;

    async function refresh() {
      const owner = ownerEl.value;
      if (!owner) {
        replaceOptions(accountEl, [], "まず所有者を選択");
        return;
      }
      const items = await fetchItems(`/kakeibo/api/accounts/?owner=${encodeURIComponent(owner)}`);
      replaceOptions(accountEl, items, "口座を選択");
    }

    ownerEl.addEventListener("change", refresh);
    await refresh(); // 初期表示
  }

  document.addEventListener("DOMContentLoaded", setupBankOwnerFilter);
})();