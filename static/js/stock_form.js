// ===========================================
// stock_form.js
// ===========================================

// 数値→カンマ区切り（小数切捨て）
function formatAmountJPY(value) {
  if (isNaN(value) || value === null) return "";
  return Math.floor(Number(value)).toLocaleString("ja-JP");
}

// 株数×単価→取得額自動計算
function calcTotalCost() {
  const sharesInput = document.getElementById("shares");
  const unitPriceInput = document.getElementById("unit_price");
  const totalDisplay = document.getElementById("total_cost");
  const totalHidden = document.getElementById("total_cost_raw");

  if (!sharesInput || !unitPriceInput || !totalDisplay || !totalHidden) return;

  const shares = Number(sharesInput.value || 0);
  const unitPrice = Number(unitPriceInput.value || 0);
  const total = shares * unitPrice;

  totalDisplay.value = shares > 0 ? formatAmountJPY(total) : "";
  totalHidden.value = shares > 0 ? Math.floor(total) : "";
}

// ===========================================
// 証券コードバリデーション
// ===========================================
function validateTicker() {
  const input = document.getElementById("ticker");
  const err = document.querySelector('.field-error[data-for="ticker"]');
  const val = (input.value || "").trim().toUpperCase();

  // 4桁数字 または 3-4桁数字+英字1文字
  const ok = /^(\d{4}|\d{3,4}[A-Z])$/.test(val);

  if (ok) {
    err.textContent = "";
  } else {
    err.textContent = "証券コードは「4桁の数字」または「数字＋英字1文字」です。";
  }
  return ok;
}

// ===========================================
// API設定
// ===========================================
const API_STOCK_BY_CODE = "/stocks/api/stock_by_code/";
const API_SUGGEST_NAME  = "/stocks/api/suggest_name/";
const API_SECTORS       = "/stocks/api/sectors/";

// ===========================================
// サーバーから33業種を取得して datalist にセット
// ===========================================
async function loadSectors() {
  try {
    const res = await fetch(API_SECTORS);
    const sectors = await res.json();
    const list = document.getElementById("sector-list");
    if (!list) return;

    list.innerHTML = "";
    sectors.forEach(sec => {
      const opt = document.createElement("option");
      opt.value = sec;
      list.appendChild(opt);
    });
  } catch (err) {
    console.error("セクター取得失敗", err);
  }
}

// ===========================================
// 証券コード入力で銘柄・セクター自動補完
// ===========================================
async function fetchByCode(code) {
  const val = (code || "").trim().toUpperCase();
  if (!/^(\d{4}|\d{3,4}[A-Z])$/.test(val)) return;

  try {
    const res = await fetch(`${API_STOCK_BY_CODE}?code=${encodeURIComponent(val)}`);
    const data = await res.json();

    if (data.success) {
      const nameInput = document.getElementById("name");
      const sectorInput = document.getElementById("sector");
      if (nameInput) nameInput.value = data.name || "";
      if (sectorInput) sectorInput.value = data.sector || "";
    }
  } catch (err) {
    console.error("銘柄取得失敗", err);
  }
}

// ===========================================
// 銘柄名サジェスト（datalist利用）
// ===========================================
async function suggestName(query) {
  if (!query || query.length < 2) return;

  try {
    const res = await fetch(`${API_SUGGEST_NAME}?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    let list = document.getElementById("name-suggest");
    if (!list) {
      list = document.createElement("datalist");
      list.id = "name-suggest";
      document.body.appendChild(list);
      const nameInput = document.getElementById("name");
      if (nameInput) nameInput.setAttribute("list","name-suggest");
    }
    list.innerHTML = "";
    data.forEach(item => {
      const opt = document.createElement("option");
      opt.value = item.name;
      opt.dataset.code = item.code;
      list.appendChild(opt);
    });
  } catch (err) {
    console.error("サジェスト失敗", err);
  }
}

// ===========================================
// 光彩エフェクト追加
// ===========================================
function addGlowEffects() {
  const form = document.getElementById("stock-form");
  if (!form) return;

  const inputs = form.querySelectorAll("input, select, textarea");
  inputs.forEach(input => {
    input.classList.add("glow");

    input.addEventListener("focus", () => form.classList.add("focus-glow"));
    input.addEventListener("blur", () => {
      if (!form.querySelector(":focus")) {
        form.classList.remove("focus-glow");
      }
    });
  });
}

// ===========================================
// 初期化
// ===========================================
document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const nameInput = document.getElementById("name");
  const form = document.getElementById("stock-form");

  // 金額自動計算
  [shares, unitPrice].forEach(el => {
    if (el) {
      el.addEventListener("input", calcTotalCost);
      el.addEventListener("change", calcTotalCost);
    }
  });

  // 証券コードバリデーション + 自動補完
  if (ticker) {
    ticker.addEventListener("input", validateTicker);
    ticker.addEventListener("blur", () => { if (validateTicker()) fetchByCode(ticker.value); });
  }

  // 銘柄サジェスト
  if (nameInput) {
    nameInput.addEventListener("input", () => suggestName(nameInput.value));
  }

  // 送信時チェック
  if (form) {
    form.addEventListener("submit", e => {
      if (!form.checkValidity() || !validateTicker()) {
        e.preventDefault();
        form.reportValidity();
      }
    });
  }

  // 初期化
  loadSectors();
  addGlowEffects();
});