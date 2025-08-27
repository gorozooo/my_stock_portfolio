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
  // 今は何もしないが、将来必要ならここに処理を追加
}

// ===== APIエンドポイント設定 =====
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
    } else {
      // 見つからなければクリア
      document.getElementById("name").value = "";
      document.getElementById("sector").value = "";
    }
  } catch (err) {
    console.error("銘柄取得失敗", err);
  }
}

// 銘柄名サジェスト（datalist 利用）
async function suggestName(query) {
  if (query.length < 2) return;
  try {
    const res = await fetch(API_SUGGEST_NAME + "?q=" + encodeURIComponent(query));
    const data = await res.json();

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

// ===== 初期化処理 =====
document.addEventListener("DOMContentLoaded", () => {
  const shares = document.getElementById("shares");
  const unitPrice = document.getElementById("unit_price");
  const ticker = document.getElementById("ticker");
  const nameInput = document.getElementById("name");
  const form = document.getElementById("stock-form");

  // 金額自動計算
  [shares, unitPrice].forEach(el => {
    el.addEventListener("input", calcTotalCost);
    el.addEventListener("change", calcTotalCost);
  });

  // 証券コードバリデーション
  ticker.addEventListener("input", validateTicker);
  ticker.addEventListener("blur", () => {
    if (validateTicker()) {
      fetchByCode(ticker.value);
    }
  });

  // 銘柄サジェスト
  nameInput.addEventListener("input", () => suggestName(nameInput.value));

  // フォーム送信時チェック
  form.addEventListener("submit", (e) => {
    const okTicker = validateTicker();
    calcTotalCost();

    if (!form.checkValidity() || !okTicker) {
      e.preventDefault();
      form.reportValidity();
    }
  });

  // 初期化
  loadSectors();
  tuneIOS();
});