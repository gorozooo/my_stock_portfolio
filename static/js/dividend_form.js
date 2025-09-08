document.addEventListener("DOMContentLoaded", () => {
  const gross = document.getElementById("gross");
  const tax = document.getElementById("tax");
  const net = document.getElementById("net-preview");
  const form = document.getElementById("div-form");
  let taxMode = "auto"; // auto or zero

  const pct = 0.20315; // 20.315%

  function toNumber(el) { return Math.max(0, parseInt((el.value || "0").replace(/,/g,""), 10) || 0); }
  function fmt(n) { return n.toLocaleString(); }

  function recalc() {
    const g = toNumber(gross);
    let t = toNumber(tax);

    if (taxMode === "auto") {
      t = Math.floor(g * pct);
      tax.value = t;
    } else if (taxMode === "zero") {
      t = 0;
      tax.value = 0;
    }

    const n = Math.max(0, g - t);
    net.textContent = g ? `¥${fmt(n)}` : "—";
  }

  gross.addEventListener("input", recalc);
  tax.addEventListener("input", () => { taxMode = "manual"; recalc(); });

  // chips
  document.querySelectorAll(".chip[data-fill]").forEach(btn => {
    btn.addEventListener("click", () => {
      const val = btn.dataset.fill;
      if (val === "all") {
        gross.focus();
        gross.select();
        return;
      }
      gross.value = toNumber(gross) + parseInt(val, 10);
      recalc();
    });
  });
  document.querySelectorAll(".chip[data-tax]").forEach(btn => {
    btn.addEventListener("click", () => {
      taxMode = btn.dataset.tax; // auto / zero
      recalc();
    });
  });

  recalc();
});

// static/js/dividend_form.js の末尾あたりに追記
(function () {
  const tickerInput = document.querySelector('input[name="ticker"]');
  const nameInput = document.querySelector('input[name="stock_name"]');
  const accountInput = document.querySelector('input[name="account_type"]');
  const brokerInput = document.querySelector('input[name="broker"]');

  let timer = null;
  function debounce(fn, wait=300) {
    clearTimeout(timer);
    timer = setTimeout(fn, wait);
  }

  async function lookup() {
    const t = (tickerInput.value || "").trim();
    if (!t) return;
    try {
      const resp = await fetch(`/api/stocks/lookup/?ticker=${encodeURIComponent(t)}`, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });
      if (resp.ok) {
        const j = await resp.json();
        if (j.found) {
          if (!nameInput.value) nameInput.value = j.stock_name || "";
          if (!accountInput.value) accountInput.value = j.account_type || "";
          if (!brokerInput.value) brokerInput.value = j.broker || "";
          // トースト代わりの軽いハイライト
          [nameInput, accountInput, brokerInput].forEach(el => {
            el.style.transition = "background 300ms";
            el.style.background = "rgba(16,185,129,0.18)";
            setTimeout(()=> el.style.background = "", 350);
          });
        }
      }
    } catch (e) { /* ネットワークエラー時は無視 */ }
  }

  if (tickerInput) {
    tickerInput.addEventListener("input", () => debounce(lookup, 300));
    tickerInput.addEventListener("change", lookup);
    tickerInput.addEventListener("blur", lookup);
  }
})();