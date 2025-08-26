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