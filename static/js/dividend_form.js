// static/js/dividend_form.js
document.addEventListener("DOMContentLoaded", () => {
  // ===== 要素 =====
  const form  = document.getElementById("div-form");
  const gross = document.getElementById("gross");            // 配当金（税引前・円）
  const tax   = document.getElementById("tax");
  const net   = document.getElementById("net-preview");

  const tickerInput   = document.querySelector('input[name="ticker"]');
  const nameInput     = document.querySelector('input[name="stock_name"]');
  const accountInput  = document.querySelector('input[name="account_type"]');
  const brokerInput   = document.querySelector('input[name="broker"]');
  const sharesInput   = document.querySelector('input[name="shares"]');          // 保有株数
  const perShareInput = document.getElementById("per-share-gross");              // 1株あたり配当（税前）

  // ===== 税計算 =====
  let taxMode = "auto";            // "auto" | "zero" | "manual"
  const pct = 0.20315;             // 20.315%

  const toNumber = (el) => Math.max(0, parseInt((el?.value || "0").replace(/,/g, ""), 10) || 0);
  const toFloat  = (el) => Math.max(0, parseFloat((el?.value || "0").replace(/,/g, "")) || 0);
  const fmt      = (n) => n.toLocaleString();

  function recalcNet() {
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

  if (gross) gross.addEventListener("input", () => {
    // ユーザーがgrossを直接編集 → 自動を一時停止（この後 shares/perShare を触ればまた自動反映される）
    grossAuto = false;
    recalcNet();
  });

  if (tax) {
    tax.addEventListener("input", () => { taxMode = "manual"; recalcNet(); });
  }

  // 金額チップ
  document.querySelectorAll(".chip[data-fill]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!gross) return;
      const val = btn.dataset.fill;
      if (val === "all") { gross.focus(); gross.select(); return; }
      // チップで操作 → 手動扱い
      gross.value = toNumber(gross) + parseInt(val, 10);
      grossAuto = false;
      recalcNet();
    });
  });

  // 税方式チップ
  document.querySelectorAll(".chip[data-tax]").forEach((btn) => {
    btn.addEventListener("click", () => {
      taxMode = btn.dataset.tax; // "auto" or "zero"
      recalcNet();
    });
  });

  // ===== 自動計算（shares × per-share → gross） =====
  let grossAuto = true;             // デフォルトON
  let programmatic = false;         // プログラム更新中フラグ（inputイベントのループ防止）

  function expectedGross() {
    const sh  = toNumber(sharesInput);
    const per = toFloat(perShareInput);
    if (sh <= 0 || per <= 0) return 0;
    return Math.round(sh * per);    // 端数は四捨五入
  }

  function updateGrossFromAuto() {
    // ユーザーがgrossを直接書き換えた直後は自動更新しない
    if (!grossAuto) return;

    const exp = expectedGross();
    if (!gross) return;
    programmatic = true;
    gross.value = exp || "";
    programmatic = false;

    recalcNet();
  }

  // shares / per-share を触るたびに自動更新
  ["input", "change", "blur"].forEach((ev) => {
    if (sharesInput)   sharesInput.addEventListener(ev, () => { grossAuto = true; updateGrossFromAuto(); });
    if (perShareInput) perShareInput.addEventListener(ev, () => { grossAuto = true; updateGrossFromAuto(); });
  });

  // ===== 銘柄自動補完（ticker→ name / account_type / broker / shares） =====
  let timer = null;
  function debounce(fn, wait = 300) { clearTimeout(timer); timer = setTimeout(fn, wait); }

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

      // 既に入力済みなら尊重。空の時だけ上書き。
      if (nameInput     && !nameInput.value)     nameInput.value     = j.stock_name   || "";
      if (accountInput  && !accountInput.value)  accountInput.value  = j.account_type || "";
      if (brokerInput   && !brokerInput.value)   brokerInput.value   = j.broker       || "";
      if (sharesInput   && !sharesInput.value)   sharesInput.value   = j.shares ?? "";

      // sharesが入ったら自動計算
      grossAuto = true;
      updateGrossFromAuto();
    } catch (_) {
      // ネットワークエラーは黙殺
    }
  }

  if (tickerInput) {
    tickerInput.addEventListener("input", () => debounce(lookup, 300));
    tickerInput.addEventListener("change", lookup);
    tickerInput.addEventListener("blur", lookup);
  }

  // 初期計算
  recalcNet();
  updateGrossFromAuto();
});