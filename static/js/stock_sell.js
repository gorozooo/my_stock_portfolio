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
  const yen = (n)=> Math.round(n).toLocaleString('ja-Jp');

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

  // Review fields (刷新)
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
    err.hidden = !msg;
    err.textContent = msg || "";
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
        limitInput.value = String(Math.max(0, Math.round(Number(val))));
      } else if (val.startsWith("+") || val.startsWith("-")) {
        const d = Number(val);
        const base = toNum(limitInput.value) || (currentPrice != null ? currentPrice : 0);
        limitInput.value = String(Math.max(0, Math.round(base + d)));
      } else {
        // "現在値" 想定
        if (currentPrice != null) limitInput.value = String(Math.round(currentPrice));
      }
      compute();
    });
  });

  [limitInput, actualProfitInput].forEach(el=> el.addEventListener("input", compute));

  /* ===== 新ルール計算 =====
     取得額 = 売却株数 × 取得単価
     売却額 = 入力した金額（市場価格 or 指値） × 売却株数
     損益   = "実際の損益額" 入力があればその値、空なら 取得額 − 売却額
     手数料 = 取得額 − 売却額 − 損益
   */
  function compute(){
    showError("");

    const qty = toNum(sharesInput.value);
    const sp  = currentSellPrice();  // 単価
    const up  = unitPrice;

    const buyAmount  = qty * up;               // 取得額（合計）
    const sellAmount = (sp != null) ? qty * sp : null; // 売却額（合計）

    // 損益
    const apText = actualProfitInput.value.trim();
    let profit;
    if (apText !== "") {
      profit = toNum(apText);
    } else {
      // 指示通り：空なら 取得額 − 売却額
      if (sellAmount == null) {
        profit = null; // 売却額が分からなければ損益は出さない
      } else {
        profit = buyAmount - sellAmount;
      }
    }

    // 手数料
    let fee = null;
    if (sellAmount != null && profit != null) {
      fee = buyAmount - sellAmount - profit;
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

    // hidden送信値
    // sell_price は「単価」を保存（合計が必要ならサーバ側で qty を掛け算）
    hiddenSellPrice.value = (sp != null) ? String(Math.round(sp)) : "";
    hiddenFee.value = (fee == null) ? "" : String(Math.round(fee));

    return {qty, sp, up, buyAmount, sellAmount, profit, fee};
  }

  /* submit validation */
  const formValidate = (e)=>{
    const { sp } = compute();
    const mode = (function(){ const r=modeRadios.find(r=>r.checked); return r? r.value : "market"; })();

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
    const q = toNum(sharesInput.value);
    if (q < 1 || q > (Number(ctx.shares)||0)){
      e.preventDefault();
      showError(`売却株数は 1〜${(Number(ctx.shares)||0).toLocaleString()} の範囲で指定してください。`);
      return;
    }
  };

  form.addEventListener("submit", formValidate);

  /* init */
  compute();

  /* スクロール系：iOS等で最下部まで行けるように安全策 */
  // 端末のソフトキーボード開閉で高さが変わっても計算し直し
  window.addEventListener("resize", () => {
    // ここではCSSのpaddingで対応済みなので表示再計算のみ
    compute();
  });
})();