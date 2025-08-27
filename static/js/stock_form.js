// 数値→カンマ区切り（小数切捨て）
function formatAmountJPY(value) {
  if (isNaN(value) || value === null) return "";
  return Math.floor(Number(value)).toLocaleString("ja-JP");
}

// 株数×単価→取得額自動計算
function calcTotalCost() {
  const shares = Number(document.getElementById("shares").value || 0);
  const unitPrice = Number(document.getElementById("unit_price").value || 0);
  const total = shares * unitPrice;
  document.getElementById("total_cost").value = shares > 0 ? formatAmountJPY(total) : "";
  document.getElementById("total_cost_raw").value = shares > 0 ? Math.floor(total) : "";
}

// 証券コード4桁バリデーション
function validateTicker() {
  const input = document.getElementById("ticker");
  const err = document.querySelector('.field-error[data-for="ticker"]');
  const val = (input.value || "").trim();
  const ok = /^\d{4}$/.test(val);
  err.textContent = ok ? "" : "証券コードは4桁の数字です。";
  return ok;
}

// API
const API_STOCK_BY_CODE = "/stocks/api/stock_by_code/";
const API_SUGGEST_NAME  = "/stocks/api/suggest_name/";
const API_SECTORS       = "/stocks/api/sectors/";

// セクター取得
async function loadSectors() {
  try {
    const res = await fetch(API_SECTORS);
    const sectors = await res.json();
    const list = document.getElementById("sector-list");
    list.innerHTML = "";
    sectors.forEach(sec => {
      const opt = document.createElement("option");
      opt.value = sec;
      list.appendChild(opt);
    });
  } catch (err) { console.error(err); }
}

// 証券コードから銘柄・セクター取得
async function fetchByCode(code) {
  if (!/^\d{4}$/.test(code)) return;
  try {
    const res = await fetch(`${API_STOCK_BY_CODE}?code=${code}`);
    const data = await res.json();
    document.getElementById("name").value = data.success ? data.name : "";
    document.getElementById("sector").value = data.success ? data.sector : "";
  } catch (err) { console.error(err); }
}

// 銘柄名サジェスト
async function suggestName(query) {
  if (query.length < 2) return;
  try {
    const res = await fetch(`${API_SUGGEST_NAME}?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    let list = document.getElementById("name-suggest");
    if (!list) {
      list = document.createElement("datalist");
      list.id = "name-suggest";
      document.body.appendChild(list);
      document.getElementById("name").setAttribute("list","name-suggest");
    }
    list.innerHTML = "";
    data.forEach(item => {
      const opt = document.createElement("option");
      opt.value = item.name;
      opt.dataset.code = item.code;
      list.appendChild(opt);
    });
  } catch (err) { console.error(err); }
}

// 初期化
document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const nameInput = document.getElementById("name");
  const form = document.getElementById("stock-form");

  [shares, unitPrice].forEach(el => el.addEventListener("input", calcTotalCost));
  [shares, unitPrice].forEach(el => el.addEventListener("change", calcTotalCost));

  ticker.addEventListener("input", validateTicker);
  ticker.addEventListener("blur", () => { if (validateTicker()) fetchByCode(ticker.value); });

  nameInput.addEventListener("input", () => suggestName(nameInput.value));

  form.addEventListener("submit", e => {
    if (!form.checkValidity() || !validateTicker()) { e.preventDefault(); form.reportValidity(); }
  });

  loadSectors();
});