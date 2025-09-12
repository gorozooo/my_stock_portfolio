(function(){
  // ========= ユーティリティ =========
  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

  // 全角/カンマ/全角マイナス等を正規化
  function normalizeNumericString(s){
    if (typeof s !== 'string') return '';
    // 全角英数記号→半角
    s = s.replace(/[！-～]/g, ch => String.fromCharCode(ch.charCodeAt(0)-0xFEE0));
    // 全角/各種ダッシュ→半角-
    s = s.replace(/[－ー―−‒–—]/g, '-');
    // カンマ・全角小数点
    s = s.replace(/,/g,'').replace(/[。．]/g,'.');
    // 先頭以外の - は削除
    s = s.replace(/(?!^)-/g,'');
    // '.' は最初の1個のみ
    const i = s.indexOf('.');
    if (i !== -1) s = s.slice(0, i+1) + s.slice(i+1).replace(/\./g, '');
    return s.trim();
  }
  function toNum(s){
    const n = parseFloat(normalizeNumericString(String(s ?? '')));
    return Number.isFinite(n) ? n : 0;
  }
  function fmtYen(n){
    const sign = n < 0 ? '-' : '';
    const v = Math.abs(Math.round(n));
    return sign + '¥' + v.toLocaleString('ja-JP');
  }

  // ========= 初期データ取得 =========
  document.addEventListener('DOMContentLoaded', () => {
    const root = $('#sellPageRoot');
    if (!root) return;

    const form        = $('#sell-form', root);
    const errorsBox   = $('#sell-errors', root);
    const noPriceBanner = $('#noPriceBanner', root);

    const sharesInit  = toNum(root.dataset.shares);
    const unitPrice   = toNum(root.dataset.unitPrice);
    const currentPx   = toNum(root.dataset.currentPrice);
    const hasPrice    = root.dataset.hasPrice === '1';
    const initialMode = root.dataset.initialMode || (hasPrice ? 'market' : 'limit');

    const qtyInput    = $('#sell-shares', root);
    const marketRadio = $('input[name="sell_mode"][value="market"]', root);
    const limitRadio  = $('input[name="sell_mode"][value="limit"]', root);
    const limitWrap   = $('#limit-wrap', root);
    const limitInput  = $('#limit-price', root);
    const actualInput = $('#actual-profit', root);

    const rvBuy  = $('#rv-buy', root);
    const rvSell = $('#rv-sell', root);
    const rvPL   = $('#rv-pl', root);
    const rvFee  = $('#rv-fee', root);

    // ========= ソフトキー初期化 =========
    function insertAtCursor(input, text){
      const start = input.selectionStart ?? input.value.length;
      const end   = input.selectionEnd ?? input.value.length;
      const before= input.value.slice(0, start);
      const after = input.value.slice(end);
      input.value = before + text + after;
      const pos = start + text.length;
      input.setSelectionRange(pos, pos);
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    function insertMinus(input){
      let v = normalizeNumericString(input.value || '');
      if (v.startsWith('-')) return;
      input.focus();
      input.setSelectionRange(0,0);
      insertAtCursor(input, '-');
    }
    function insertDot(input){
      let v = normalizeNumericString(input.value || '');
      if (v.includes('.')) return;
      insertAtCursor(input, '.');
    }
    function toggleSign(input){
      let v = normalizeNumericString(input.value || '');
      if (!v){ input.value = '-'; input.setSelectionRange(1,1); input.dispatchEvent(new Event('input',{bubbles:true})); return; }
      v = v.startsWith('-') ? v.slice(1) : ('-' + v);
      input.value = v;
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    function clearAll(input){
      input.value = '';
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    $$('.softpad', root).forEach(pad=>{
      const targetSel = pad.getAttribute('data-target');
      const input = $(targetSel, root);
      if (!input) return;
      pad.addEventListener('click', e=>{
        const btn = e.target.closest('.sp-key');
        if (!btn) return;
        if (btn.classList.contains('sp-minus'))  insertMinus(input);
        else if (btn.classList.contains('sp-dot')) insertDot(input);
        else if (btn.classList.contains('sp-toggle')) toggleSign(input);
        else if (btn.classList.contains('sp-clear'))  clearAll(input);
        input.focus();
      });
      input.addEventListener('input', ()=>{
        const before = input.value;
        const pos = input.selectionStart ?? before.length;
        const norm = normalizeNumericString(before);
        if (before !== norm){
          input.value = norm;
          const newPos = Math.min(norm.length, pos);
          input.setSelectionRange(newPos, newPos);
        }
      });
    });

    // ========= 売却方法トグル =========
    function applyMode(){
      const isLimit = !!(limitRadio && limitRadio.checked);
      if (limitWrap)  limitWrap.hidden = !isLimit;
      if (limitInput){
        limitInput.required = isLimit;
        limitInput.disabled = !isLimit;
      }
      if (noPriceBanner) noPriceBanner.hidden = hasPrice || !(!hasPrice && !isLimit);
      updateReview();
    }
    if (marketRadio && limitRadio){
      marketRadio.addEventListener('change', applyMode);
      limitRadio.addEventListener('change', applyMode);
      // 初期モード適用
      if (initialMode === 'limit' && limitRadio) limitRadio.checked = true;
      if (initialMode === 'market' && marketRadio) marketRadio.checked = true;
      applyMode();
    }

    // ========= 数量ヘルパー =========
    $$('.qty-btn', root).forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const step = parseInt(btn.dataset.step || '0', 10);
        const cur = parseInt(qtyInput.value || '0', 10);
        const max = parseInt(qtyInput.max || '0', 10);
        let next = cur + step;
        next = Math.max(1, Math.min(max, next));
        qtyInput.value = String(next);
        updateReview();
      });
    });
    $$('.chip[data-fill]', root).forEach(ch=>{
      ch.addEventListener('click', ()=>{
        const pct = parseInt(ch.dataset.fill || '0', 10);
        const max = parseInt(qtyInput.max || '0', 10);
        const val = Math.max(1, Math.floor(max * pct / 100));
        qtyInput.value = String(val);
        updateReview();
      });
    });
    qtyInput.addEventListener('input', updateReview);

    // 指値ヒント（±5/±10/現在値）
    $$('.limit-hints .chip', root).forEach(ch=>{
      ch.addEventListener('click', ()=>{
        if (!limitInput) return;
        const v = ch.dataset.limit || '';
        if (v === '+5' || v === '+10' || v === '-5' || v === '-10'){
          const delta = parseFloat(v);
          const cur = toNum(limitInput.value || 0);
          limitInput.value = normalizeNumericString(String(cur + delta));
        } else {
          limitInput.value = normalizeNumericString(v);
        }
        limitInput.dispatchEvent(new Event('input', {bubbles:true}));
        updateReview();
      });
    });
    if (limitInput) limitInput.addEventListener('input', updateReview);
    if (actualInput) actualInput.addEventListener('input', updateReview);

    // ========= レビュー計算 =========
    function effectivePricePerShare(){
      if (limitRadio && limitRadio.checked){
        return toNum(limitInput?.value || 0);
      }
      // market
      return currentPx; // 0 の場合は売却額も 0 になる（指値推奨）
    }
    function updateReview(){
      const shares = parseInt(qtyInput.value || '0', 10) || 0;
      const buyAmt = unitPrice * shares;

      const px = effectivePricePerShare();
      const sellAmt = px * shares;

      const actual = normalizeNumericString(actualInput?.value || '');
      const hasActual = actual !== '' && actual !== '-' && actual !== '.';
      const actualVal = hasActual ? toNum(actual) : null;

      // 損益：実際の損益があれば採用。なければ 売却額 − 取得額
      const profit = (actualVal !== null) ? actualVal : (sellAmt - buyAmt);

      // 手数料：売却額 − 取得額 − 損益（損益±どちらでも成立）
      const fee = sellAmt - buyAmt - profit;

      rvBuy.textContent  = fmtYen(buyAmt);
      rvSell.textContent = fmtYen(sellAmt);
      rvPL.textContent   = fmtYen(profit);
      rvFee.textContent  = fmtYen(fee);
    }
    // 初期描画
    if (!qtyInput.value) qtyInput.value = String(sharesInit || 1);
    updateReview();

    // ========= 送信時：正規化して検証はサーバに任せる =========
    if (form){
      form.addEventListener('submit', ()=>{
        if (limitInput) limitInput.value = normalizeNumericString(limitInput.value || '');
        if (actualInput) actualInput.value = normalizeNumericString(actualInput.value || '');
      });
    }
  });
})();