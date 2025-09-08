// static/js/dividend_form.js
document.addEventListener("DOMContentLoaded", () => {
  // ===== 要素参照 =====
  const form  = document.getElementById("div-form");
  const gross = document.getElementById("gross");
  const tax   = document.getElementById("tax");
  const net   = document.getElementById("net-preview");

  const tickerInput  = document.querySelector('input[name="ticker"]');
  const nameInput    = document.querySelector('input[name="stock_name"]');
  const accountInput = document.querySelector('input[name="account_type"]');
  const brokerInput  = document.querySelector('input[name="broker"]');
  const sharesInput  = document.querySelector('input[name="shares"]'); // ★ 追加：保有株数

  // ===== 税計算モード =====
  let taxMode = "auto";            // "auto" | "zero" | "manual"
  const pct = 0.20315;             // 20.315%

  // ===== ユーティリティ =====
  const toNumber = (el) => Math.max(0, parseInt((el?.value || "0").replace(/,/g, ""), 10) || 0);
  const fmt      = (n) => n.toLocaleString();

  // 軽いハイライト（自動補完されたフィールドに視覚フィードバック）
  function flash(el) {
    if (!el) return;
    el.style.transition = "background 300ms";
    const old = el.style.background;
    el.style.background = "rgba(16,185,129,0.18)";
    setTimeout(() => (el.style.background = old || ""), 350);
  }

  // ===== 受取額プレビュー再計算 =====
  function recalc() {
    const g = toNumber(gross);
    let t   = toNumber(tax);

    if (taxMode === "auto") {
      t = Math.floor(g * pct);
      if (tax) tax.value = t;
    } else if (taxMode === "zero") {
      t = 0;
      if (tax) tax.value = 0;
    }
    const n = Math.max(0, g - t);
    if (net) net.textContent = g ? `¥${fmt(n)}` : "—";
  }

  // 入力イベント
  if (gross) gross.addEventListener("input", recalc);
  if (tax)   tax.addEventListener("input", () => { taxMode = "manual"; recalc(); });

  // 金額チップ（+1000 / +5000 / +10000 / ALL）
  document.querySelectorAll(".chip[data-fill]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const val = btn.dataset.fill;
      if (!gross) return;
      if (val === "all") {
        gross.focus();
        gross.select();
        return;
      }
      gross.value = toNumber(gross) + parseInt(val, 10);
      recalc();
    });
  });

  // 税方式チップ（自動 / ゼロ）
  document.querySelectorAll(".chip[data-tax]").forEach((btn) => {
    btn.addEventListener("click", () => {
      taxMode = btn.dataset.tax; // "auto" or "zero"
      recalc();
    });
  });

  // ===== 銘柄自動補完（ticker→ name / account_type / broker / shares） =====
  let timer = null;
  function debounce(fn, wait = 300) {
    clearTimeout(timer);
    timer = setTimeout(fn, wait);
  }

  async function lookup() {
    const t = (tickerInput?.value || "").trim();
    if (!t) return;

    try {
      const resp = await fetch(`/api/stocks/lookup/?ticker=${encodeURIComponent(t)}`, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });

      if (!resp.ok) return; // 404などは無視
      const j = await resp.json();
      if (!j.found) return;

      // 値が空の時だけ上書き（ユーザー入力は尊重）
      if (nameInput     && !nameInput.value)     { nameInput.value     = j.stock_name   || ""; flash(nameInput); }
      if (accountInput  && !accountInput.value)  { accountInput.value  = j.account_type || ""; flash(accountInput); }
      if (brokerInput   && !brokerInput.value)   { brokerInput.value   = j.broker       || ""; flash(brokerInput); }
      if (sharesInput   && !sharesInput.value)   { sharesInput.value   = j.shares ?? "";     flash(sharesInput); }
    } catch (_) {
      // ネットワークエラー等は黙殺
    }
  }

  if (tickerInput) {
    tickerInput.addEventListener("input", () => debounce(lookup, 300));
    tickerInput.addEventListener("change", lookup);
    tickerInput.addEventListener("blur", lookup);
  }

  // 初期計算
  recalc();
});