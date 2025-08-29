// ================================
// stock_form.js（スマホ対応版）
// ================================

// 数値→カンマ区切り
function formatAmountJPY(value) {
  if (isNaN(value) || value === null) return "";
  return Math.floor(Number(value)).toLocaleString("ja-JP");
}

// 株数×単価→取得額
function calcTotalCost() {
  const shares = Number(document.getElementById("shares")?.value || 0);
  const unitPrice = Number(document.getElementById("unit_price")?.value || 0);
  const total = shares * unitPrice;
  document.getElementById("total_cost").value = shares > 0 ? formatAmountJPY(total) : "";
  document.getElementById("total_cost_raw").value = shares > 0 ? Math.floor(total) : "";
}

// 証券コードバリデーション
function validateTicker() {
  const val = (document.getElementById("ticker")?.value || "").trim().toUpperCase();
  const err = document.querySelector('.field-error[data-for="ticker"]');
  const ok = /^(\d{4}|\d{3,4}[A-Z])$/.test(val);
  if (err) err.textContent = ok ? "" : "証券コードは「4桁の数字」または「数字＋英字1文字」です。";
  return ok;
}

// API URL
const API_STOCK_BY_CODE = "/stocks/api/stock_by_code/";
const API_SECTORS       = "/stocks/api/sectors/";
const API_SUGGEST_NAME  = "/stocks/api/suggest_name/";

// 証券コード → 銘柄・セクター自動補完
async function fetchByCode(code) {
  if (!/^(\d{4}|\d{3,4}[A-Z])$/.test(code)) return;
  try {
    const res = await fetch(`${API_STOCK_BY_CODE}?code=${encodeURIComponent(code)}`);
    const data = await res.json();
    document.getElementById("name").value   = data.success ? data.name   : "";
    document.getElementById("sector").value = data.success ? data.sector : "";
  } catch (err) {
    console.error(err);
  }
}

// 33業種リスト取得
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
  } catch (err) { console.error(err); }
}

// 光彩エフェクト
function addGlowEffects() {
  const form = document.getElementById("stock-form");
  if (!form) return;
  form.querySelectorAll("input, select, textarea").forEach(input => {
    input.classList.add("glow");
    input.addEventListener("focus", () => form.classList.add("focus-glow"));
    input.addEventListener("blur", () => { if (!form.querySelector(":focus")) form.classList.remove("focus-glow"); });
  });
}

// 初期化
document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const nameInput = document.getElementById("name");
  const form = document.getElementById("stock-form");

  // 初回計算
  calcTotalCost();

  // 再計算
  [shares, unitPrice].forEach(el => {
    if (el) el.addEventListener("input", calcTotalCost);
  });

  // 証券コード入力 → 自動補完
  if (ticker) {
    ticker.addEventListener("input", () => { if (validateTicker()) fetchByCode(ticker.value.toUpperCase()); });
    ticker.addEventListener("blur", () => { if (validateTicker()) fetchByCode(ticker.value.toUpperCase()); });
  }

  // 初期化
  loadSectors();
  addGlowEffects();
});
