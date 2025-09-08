document.addEventListener("DOMContentLoaded", () => {
  const amount = document.getElementById("amount");
  const form   = document.getElementById("cash-form");

  // 数字整形
  const toDigits = (s) => (s || "").replace(/[^\d]/g, "");
  const fmt      = (n) => (n ? Number(n).toLocaleString() : "");

  function setValFromDigits(digits) {
    digits = digits.replace(/^0+/, ""); // 先頭0除去
    amount.value = fmt(digits);
  }

  // 直入力対応
  amount.addEventListener("input", () => setValFromDigits(toDigits(amount.value)));
  amount.addEventListener("focus", () => amount.select());

  // チップ
  document.querySelectorAll(".chip[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cur = Number(toDigits(amount.value) || "0");
      const add = Number(btn.dataset.add || "0");
      setValFromDigits(String(cur + add));
    });
  });
  document.querySelectorAll(".chip[data-clear]").forEach((btn) => {
    btn.addEventListener("click", () => setValFromDigits(""));
  });

  // テンキー
  document.querySelectorAll(".numpad [data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cur = toDigits(amount.value);
      setValFromDigits(cur + String(btn.dataset.key));
    });
  });
  const back = document.querySelector(".numpad [data-back]");
  if (back) back.addEventListener("click", () => {
    const cur = toDigits(amount.value);
    setValFromDigits(cur.slice(0, -1));
  });

  // 送信時はカンマを除去
  form.addEventListener("submit", () => {
    amount.value = toDigits(amount.value);
  });
});