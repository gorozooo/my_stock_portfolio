// 数値→カンマ区切り（小数切捨て）表示
function formatAmountJPY(value) {
  if (isNaN(value) || value === null) return "";
  const floored = Math.floor(Number(value)); // 小数切捨て
  return floored.toLocaleString("ja-JP");
}

function calcTotalCost() {
  const shares = Number(document.getElementById("shares").value || 0);
  const unitPrice = Number(document.getElementById("unit_price").value || 0);
  const total = shares * unitPrice;
  const display = document.getElementById("total_cost");
  const hidden = document.getElementById("total_cost_raw");

  if (shares > 0 && unitPrice >= 0) {
    display.value = formatAmountJPY(total);
    hidden.value = Math.floor(total); // 整数値を送信
  } else {
    display.value = "";
    hidden.value = "";
  }
}

// 4桁バリデーション（証券コード）
function validateTicker() {
  const input = document.getElementById("ticker");
  const err = document.querySelector('.field-error[data-for="ticker"]');
  const val = (input.value || "").trim();
  const ok = /^\d{4}$/.test(val);
  if (!ok) {
    err.textContent = "証券コードは4桁の数字です。";
  } else {
    err.textContent = "";
  }
  return ok;
}

// iOSのtype=numberで上下ボタンが邪魔な場合の微調整（任意）
function tuneIOS() {
  // ここでは特に何もしないが将来の拡張用
}

document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const form = document.getElementById("stock-form");

  [shares, unitPrice].forEach(el => {
    el.addEventListener("input", calcTotalCost);
    el.addEventListener("change", calcTotalCost);
  });

  ticker.addEventListener("input", validateTicker);
  ticker.addEventListener("blur", validateTicker);

  form.addEventListener("submit", (e) => {
    const okTicker = validateTicker();
    calcTotalCost();

    // 必須チェック（ブラウザのHTML5検証を尊重）
    if (!form.checkValidity() || !okTicker) {
      e.preventDefault();
      form.reportValidity();
    }
  });

  tuneIOS();
});

// APIのURL (Django側urls.pyに合わせて)
const API_STOCK_BY_CODE = "/stocks/api/stock_by_code/";
const API_SUGGEST_NAME  = "/stocks/api/suggest_name/";
const API_SECTORS       = "/stocks/api/sectors/";

// 33業種をサーバーから取得して datalist にセット
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
  } catch (err) {
    console.error("セクター取得失敗", err);
  }
}

// 証券コード入力で銘柄・セクター自動補完
async function fetchByCode(code) {
  if (!/^\d{4}$/.test(code)) return;
  try {
    const res = await fetch(API_STOCK_BY_CODE + "?code=" + code);
    const data = await res.json();
    if (data.success) {
      document.getElementById("name").value = data.name;
      document.getElementById("sector").value = data.sector;
    }
  } catch (err) {
    console.error("銘柄取得失敗", err);
  }
}

// 銘柄名サジェスト（簡易版: コンソール表示 or 自動補完リスト）
let suggestBox;
async function suggestName(query) {
  if (query.length < 2) return;
  try {
    const res = await fetch(API_SUGGEST_NAME + "?q=" + encodeURIComponent(query));
    const data = await res.json();

    // datalistで候補を出す
    let list = document.getElementById("name-suggest");
    if (!list) {
      list = document.createElement("datalist");
      list.id = "name-suggest";
      document.body.appendChild(list);
      document.getElementById("name").setAttribute("list", "name-suggest");
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

document.addEventListener("DOMContentLoaded", () => {
  loadSectors();

  const ticker = document.getElementById("ticker");
  ticker.addEventListener("blur", () => fetchByCode(ticker.value));

  const nameInput = document.getElementById("name");
  nameInput.addEventListener("input", () => suggestName(nameInput.value));
});