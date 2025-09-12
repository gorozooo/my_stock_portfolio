// static/js/stock_sell.js
(function(){
  const ctx = window.__SELL_CTX__ || {};
  const $  = (s, r=document)=> r.querySelector(s);
  const $$ = (s, r=document)=> Array.from(r.querySelectorAll(s));
  const toNum = (t) => {
    if (t === null || t === undefined) return 0;
    const s = String(t).replace(/[^\-0-9.]/g,"");
    if (!s || s === "-" || s === ".") return 0;
    const v = parseFloat(s);
    return isNaN(v) ? 0 : v;
  };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const yen = (n)=> Math.round(n).toLocaleString('ja-JP');

  // Elements
  const form = $("#sell-form");
  const err  = $("#sell-errors");
  const sharesInput = $("#sell-shares");
  const qtyBtns = $$(".qty-btn");
  const fillChips = $$(".qty-helpers .chip");

  const modeRadios = $$("input[name='sell_mode']");
  const limitWrap  = $("#limit-wrap");
  const limitInput = $("#limit-price");
  const limitChips = $$(".limit-hints .chip");

  const actualProfitInput = $("#actual-profit");

  // Review fields
  const rvBuy  = $("#rv-buy");
  const rvSell = $("#rv-sell");
  const rvPL   = $("#rv-pl");
  const rvFee  = $("#rv-fee");

  const hiddenFee = $("#hidden-fee");
  const hiddenSellPrice = $("#hidden-sell-price");

  // Context
  const MAX = Number(ctx.shares) || 0;
  const unitPrice = Number(ctx.unit_price) || 0;
  const currentPrice = (ctx.current_price !== null && ctx.current_price !== undefined) ? Number(ctx.current_price) : null;

  /* helpers */
  function activeMode(){
    const r = modeRadios.find(r=>r.checked);
    return r ? r.value : "market";
  }
  function currentSellPrice(){
    if (activeMode() === "market"){
      return currentPrice; // 市場価格（取得できない場合は null）
    }
    const lim = toNum(limitInput.value);
    return lim > 0 ? lim : null;
  }
  function showError(msg){
    if (!err) return;
    if (msg) {
      err.hidden = false;
      err.textContent = msg;
    } else {
      err.hidden = true;
      err.textContent = "";
    }
  }

  /* UI events */
  qtyBtns.forEach(b=>{
    b.addEventListener("click", ()=>{
      const step = Number(b.dataset.step) || 0;
      let v = toNum(sharesInput.value) + step;
      v = clamp(v, 1, MAX);
      sharesInput.value = String(v);
      compute();
    });
  });
  fillChips.forEach(c=>{
    c.addEventListener("click", ()=>{
      const pct = Number(c.dataset.fill);
      let v = Math.floor(MAX * pct / 100);
      v = clamp(v, 1, MAX);
      sharesInput.value = String(v);
      compute();
    });
  });
  sharesInput.addEventListener("input", ()=>{
    let v = Math.floor(toNum(sharesInput.value));
    if (!v || v < 1) v = 1;
    if (v > MAX) v = MAX;
    sharesInput.value = String(v);
    compute();
  });

  modeRadios.forEach(r=>{
    r.addEventListener("change", ()=>{
      const isLimit = (r.value === "limit" && r.checked);
      limitWrap.hidden = !isLimit;
      compute();
    });
  });

  limitChips.forEach(ch=>{
    ch.addEventListener("click", ()=>{
      const val = ch.dataset.limit;
      if (!val) return;
      if (!isNaN(Number(val))) {
        // 数値そのまま
        limitInput.value = String(Math.max(0, Math.round(Number(val))));
      } else if (val.startsWith("+") || val.startsWith("-")) {
        // 相対
        const d = Number(val);
        const base = toNum(limitInput.value) || (currentPrice != null ? currentPrice : 0);
        limitInput.value = String(Math.max(0, Math.round(base + d)));
      } else {
        // "現在値"
        if (currentPrice != null) limitInput.value = String(Math.round(currentPrice));
      }
      compute();
    });
  });

  [limitInput, actualProfitInput].forEach(el=> el.addEventListener("input", compute));

  /* ===== 計算（サマリー＆hidden） =====
     基本式：損益 = 売却額 − 取得額 − 手数料
     → 手数料 = 売却額 − 取得額 − 損益

     表示＆送信
     - 取得額 = 売却株数 × 取得単価
     - 売却額 = （市場価格 or 指値）× 売却株数
     - 損益   = 「実際の損益額」入力があればその値、空なら 売却額 − 取得額
     - 手数料 = 売却額 − 取得額 − 損益
  */
  function compute(){
    showError("");

    const qty = toNum(sharesInput.value);
    const sp  = currentSellPrice();  // 売却単価（null あり）
    const up  = unitPrice;

    const buyAmount  = qty * up;                         // 取得額（合計）
    const sellAmount = (sp != null) ? qty * sp : null;   // 売却額（合計）

    // 損益（入力優先 / 未入力は 売却額 − 取得額）
    const apText = actualProfitInput.value.trim();
    let profit;
    if (apText !== "") {
      profit = toNum(apText);
    } else {
      profit = (sellAmount == null) ? null : (sellAmount - buyAmount);
    }

    // 手数料（常に一意に決まる）
    let fee = null;
    if (sellAmount != null && profit != null) {
      fee = sellAmount - buyAmount - profit;
    }

    // 表示
    rvBuy.textContent  = `¥${yen(buyAmount)}`;
    rvSell.textContent = (sellAmount == null) ? "—" : `¥${yen(sellAmount)}`;
    if (profit == null) {
      rvPL.textContent = "—";
      rvPL.classList.remove("profit","loss");
    } else {
      rvPL.textContent = `${profit>=0?"+":""}¥${yen(profit)}`;
      rvPL.classList.toggle("profit", profit>=0);
      rvPL.classList.toggle("loss", profit<0);
    }
    rvFee.textContent = (fee == null) ? "—" : `¥${yen(fee)}`;

    // hidden送信値（単価/手数料）
    hiddenSellPrice.value = (sp != null) ? String(Math.round(sp)) : "";
    hiddenFee.value = (fee == null) ? "" : String(Math.round(fee));

    return {qty, sp, up, buyAmount, sellAmount, profit, fee};
  }

  // ★送信はサーバに任せる：preventDefault しない
  form.addEventListener("submit", ()=>{
    // 送信直前に hidden を確定
    compute();
    // 何も止めずにそのままPOST
  });

  /* init */
  compute();

  // モバイルキーボードの高さ変化でも再計算
  window.addEventListener("resize", compute);
})();