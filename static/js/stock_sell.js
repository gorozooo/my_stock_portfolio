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

  const estAmount = $("#est-amount");
  const estPL     = $("#est-pl");
  const estFee    = $("#est-fee");

  const hiddenFee = $("#hidden-fee");
  const hiddenSellPrice = $("#hidden-sell-price");

  // State
  const MAX = Number(ctx.shares) || 0;
  const unitPrice = Number(ctx.unit_price) || 0;
  const currentPrice = (ctx.current_price !== null && ctx.current_price !== undefined) ? Number(ctx.current_price) : null;

  // Helpers
  function activeMode(){
    const r = modeRadios.find(r=>r.checked);
    return r ? r.value : "market";
  }
  function currentSellPrice(){
    if (activeMode() === "market"){
      return currentPrice; // 現在値がない場合は null
    }
    const lim = toNum(limitInput.value);
    return lim > 0 ? lim : null;
  }
  function showError(msg){
    if (!err) return;
    err.hidden = !msg;
    err.textContent = msg || "";
  }

  // UI events
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
      if (val === "現在値"){
        if (currentPrice != null) limitInput.value = String(Math.round(currentPrice));
      }else if (val.startsWith("+") || val.startsWith("-")){
        const d = Number(val);
        const base = toNum(limitInput.value) || (currentPrice != null ? currentPrice : 0);
        limitInput.value = String(Math.max(0, Math.round(base + d)));
      }else{
        limitInput.value = String(Math.max(0, Math.round(toNum(val))));
      }
      compute();
    });
  });
  limitInput.addEventListener("input", compute);
  actualProfitInput.addEventListener("input", compute);

  // Core compute
  function compute(){
    showError("");
    const qty = toNum(sharesInput.value);
    const sp  = currentSellPrice(); // may be null
    const up  = unitPrice;

    // 売却額
    let sellAmt = null;
    if (sp != null) sellAmt = qty * sp;

    // 概算損益（売買差額ベース）
    let grossPL = null;
    if (sp != null) grossPL = (sp - up) * qty;

    // 手数料（仕様通り：手数料 = 概算売却額 − 実際の損益額、未入力なら 0）
    const ap = actualProfitInput.value.trim();
    let fee = 0;
    if (ap !== "" && sellAmt != null){
      fee = sellAmt - toNum(ap);
      // 手数料は負になり得ない前提（負なら0に丸め）
      if (fee < 0) fee = 0;
    }

    // 表示
    estAmount.textContent = (sellAmt == null) ? "—" : `¥${yen(sellAmt)}`;
    estPL.textContent     = (grossPL == null) ? "—" : `${grossPL>=0?"+":""}¥${yen(grossPL)}`;
    estPL.classList.toggle("profit", grossPL!=null && grossPL>=0);
    estPL.classList.toggle("loss",   grossPL!=null && grossPL<0);
    estFee.textContent    = (ap !== "" && sellAmt != null) ? `¥${yen(fee)}` : "—";

    // hidden
    hiddenFee.value = String(Math.round(fee || 0));
    hiddenSellPrice.value = (sp != null) ? String(Math.round(sp)) : "";

    return {qty, sp, up, sellAmt, grossPL, fee};
  }

  // Validate on submit
  form.addEventListener("submit", (e)=>{
    const { sp } = compute();
    const mode = activeMode();

    if (mode === "market" && (sp == null)){
      e.preventDefault();
      showError("現在値を取得できないため、市場価格での売却が行えません。指値に切り替えて価格を入力してください。");
      return;
    }
    if (mode === "limit"){
      const v = toNum(limitInput.value);
      if (v <= 0){
        e.preventDefault();
        showError("指値価格を入力してください。");
        return;
      }
    }
    // 数量
    const q = toNum(sharesInput.value);
    if (q < 1 || q > MAX){
      e.preventDefault();
      showError(`売却株数は 1〜${MAX.toLocaleString()} の範囲で指定してください。`);
      return;
    }
  });

  // Init
  compute();
})();