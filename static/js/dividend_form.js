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