// ================================
// stock_form.js（スマホ完全対応版）
// ================================

// 数値→カンマ区切り
function formatAmountJPY(value) {
  if (isNaN(value) || value === null) return "";
  return Math.floor(Number(value)).toLocaleString("ja-JP");
}

// 株数×単価→取得額
function calcTotalCost() {
  const sharesInput = document.getElementById("shares");
  const unitPriceInput = document.getElementById("unit_price");
  const totalCostDisplay = document.getElementById("total_cost");
  const totalCostRaw = document.getElementById("total_cost_raw");

  if (!sharesInput || !unitPriceInput || !totalCostDisplay || !totalCostRaw) return;

  const shares = Number(sharesInput.value || 0);
  const unitPrice = Number(unitPriceInput.value || 0);
  const total = shares * unitPrice;

  totalCostDisplay.value = shares > 0 ? formatAmountJPY(total) : "";
  totalCostRaw.value = shares > 0 ? Math.floor(total) : "";
}

// 証券コードバリデーション
function validateTicker() {
  const tickerInput = document.getElementById("ticker");
  if (!tickerInput) return false;

  const val = (tickerInput.value || "").trim().toUpperCase();
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

    const nameInput = document.getElementById("name");
    const sectorInput = document.getElementById("sector");

    if (nameInput)   nameInput.value   = data.success ? data.name   : "";
    if (sectorInput) sectorInput.value = data.success ? data.sector : "";
  } catch (err) {
    console.error("証券コード取得失敗", err);
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
  } catch (err) {
    console.error("セクター取得失敗", err);
  }
}

// 光彩エフェクト
function addGlowEffects() {
  const form = document.getElementById("stock-form");
  if (!form) return;

  form.querySelectorAll("input, select, textarea").forEach(input => {
    input.classList.add("glow");

    input.addEventListener("focus", () => form.classList.add("focus-glow"));
    input.addEventListener("blur", () => {
      if (!form.querySelector(":focus")) form.classList.remove("focus-glow");
    });
  });
}

// 初期化
document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const form = document.getElementById("stock-form");

  // 初回計算
  calcTotalCost();

  // 株数・単価入力時に再計算
  [shares, unitPrice].forEach(el => {
    if (!el) return;
    el.addEventListener("input", calcTotalCost);
    el.addEventListener("change", calcTotalCost); // モバイル対応
  });

  // 証券コード入力 → 自動補完
  if (ticker) {
    const fetchHandler = () => {
      const val = ticker.value.toUpperCase().trim();
      if (validateTicker()) fetchByCode(val);
    };
    ticker.addEventListener("input", fetchHandler);
    ticker.addEventListener("change", fetchHandler);
    ticker.addEventListener("blur", fetchHandler);
  }

  // 初期化
  loadSectors();
  addGlowEffects();
});
