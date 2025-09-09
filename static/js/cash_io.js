document.addEventListener("DOMContentLoaded", () => {
  const amount = document.getElementById("amount");
  const form = document.getElementById("cash-form");

  // 数字だけを抽出 → 先頭ゼロ除去
  const toNumber = (s) => {
    const n = (s || "").replace(/[^\d]/g, "");
    return n.replace(/^0+/, "") || "0";
  };

  // 桁区切りフォーマット
  const fmt = (digits) => {
    const n = digits || "0";
    return n.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  };

  // 入力フィールドをフォーカス時は素の数字、blurでカンマ付きに
  function setAmountRaw(digits) {
    amount.value = digits;
  }
  function setAmountFormatted(digits) {
    amount.value = fmt(digits);
  }

  // 入力ハンドラ
  amount.addEventListener("input", () => {
    const digits = toNumber(amount.value);
    setAmountRaw(digits);
  });
  amount.addEventListener("focus", () => {
    amount.setSelectionRange(amount.value.length, amount.value.length);
  });
  amount.addEventListener("blur", () => {
    const digits = toNumber(amount.value);
    setAmountFormatted(digits);
  });

  // 初期フォーマット
  setAmountFormatted(toNumber(amount.value));

  // チップ (+1000等 / クリア)
  document.querySelectorAll(".chip[data-add]").forEach(btn => {
    btn.addEventListener("click", () => {
      const add = parseInt(btn.dataset.add, 10) || 0;
      const cur = parseInt(toNumber(amount.value), 10) || 0;
      const next = String(cur + add);
      setAmountFormatted(next);
    });
  });
  document.querySelectorAll(".chip[data-clear]").forEach(btn => {
    btn.addEventListener("click", () => setAmountFormatted("0"));
  });

  // テンキー
  document.querySelectorAll(".numpad button[data-key]").forEach(btn => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      let digits = toNumber(amount.value);
      // 先頭ゼロの扱い
      if (digits === "0") digits = "";
      setAmountFormatted(digits + key);
    });
  });
  const backBtn = document.querySelector(".numpad button[data-back]");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      let digits = toNumber(amount.value);
      if (digits.length <= 1) {
        setAmountFormatted("0");
      } else {
        setAmountFormatted(digits.slice(0, -1));
      }
    });
  }

  // 送信時：数値は素の数字でPOST（サーバはint）
  if (form) {
    form.addEventListener("submit", () => {
      amount.value = toNumber(amount.value);
    });
  }
});