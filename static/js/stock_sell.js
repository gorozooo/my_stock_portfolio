// ==========================
// 売却ページJS（スマホ優先）
// ==========================
(() => {
  const ctx = window.__SELL_CTX__ || {};
  const form = document.getElementById('sell-form');
  if (!form) return;

  const errorsEl = document.getElementById('sell-errors');
  const sharesEl = document.getElementById('sell-shares');
  const modeRadios = Array.from(form.querySelectorAll('input[name="sell_mode"]'));
  const limitWrap = document.getElementById('limit-wrap');
  const limitInput = document.getElementById('limit-price');

  const estAmountEl = document.getElementById('est-amount');
  const estPlEl = document.getElementById('est-pl');

  const totalOwned = Number(ctx.shares || 0);
  const unit = Number(ctx.unit_price || 0);
  const current = ctx.current_price == null ? null : Number(ctx.current_price);

  // --- helper ---
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  const showErrors = (msgs) => {
    if (!errorsEl) return;
    if (!msgs || !msgs.length) {
      errorsEl.hidden = true;
      errorsEl.innerHTML = '';
      return;
    }
    errorsEl.hidden = false;
    errorsEl.innerHTML = msgs.map(m => `<div>• ${m}</div>`).join('');
  };

  const activeMode = () => {
    const r = modeRadios.find(r => r.checked);
    return r ? r.value : 'market';
  };

  const getQty = () => clamp(Number(sharesEl.value || 0), 1, totalOwned);

  const getPrice = () => {
    if (activeMode() === 'market') {
      return current ?? unit; // 現在値が無ければ取得単価ベース
    }
    return Number(limitInput.value || 0);
  };

  const formatJPY = (n) => isFinite(n) ? '¥' + Math.round(n).toLocaleString() : '—';

  const recompute = () => {
    const qty = getQty();
    sharesEl.value = qty;

    // 指値欄の表示
    if (activeMode() === 'limit') {
      limitWrap.hidden = false;
    } else {
      limitWrap.hidden = true;
    }

    // 見積もり
    const price = getPrice();
    const estAmount = qty * price;
    const cost = qty * unit;
    const pl = estAmount - cost;

    estAmountEl.textContent = formatJPY(estAmount);
    estPlEl.textContent = (isFinite(pl) ? (pl >= 0 ? '+' : '') : '') + formatJPY(pl).replace('¥', '');
    estPlEl.classList.toggle('pos', isFinite(pl) && pl >= 0);
    estPlEl.classList.toggle('neg', isFinite(pl) && pl < 0);
  };

  // 初期化
  recompute();

  // 数量ボタン
  form.querySelectorAll('.qty-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const step = Number(btn.dataset.step || 0);
      sharesEl.value = clamp(Number(sharesEl.value || 0) + step, 1, totalOwned);
      recompute();
    });
  });

  // パーセンテージ／ALL
  form.querySelectorAll('.qty-helpers .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const pct = Number(chip.dataset.fill || 0);
      if (!pct) return;
      const v = Math.max(1, Math.floor(totalOwned * pct / 100));
      sharesEl.value = clamp(v, 1, totalOwned);
      recompute();
    });
  });

  sharesEl.addEventListener('input', recompute);

  // 売却方法切替
  modeRadios.forEach(r => r.addEventListener('change', recompute));

  // 指値チップ
  form.querySelectorAll('.limit-hints .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const val = chip.dataset.limit;
      if (!val) return;
      if (val === '+5' || val === '+10' || val === '-5' || val === '-10') {
        const delta = Number(val);
        const base = (current ?? unit);
        limitInput.value = Math.max(0, Math.round(base + delta));
      } else {
        limitInput.value = Math.max(0, Math.round(Number(val)));
      }
      recompute();
    });
  });

  limitInput.addEventListener('input', recompute);

  // 送信バリデーション（簡易）
  form.addEventListener('submit', (e) => {
    const errs = [];
    const qty = getQty();

    if (!qty || qty < 1) errs.push('売却株数を1以上で指定してください。');
    if (qty > totalOwned) errs.push('保有株数を超えています。');

    if (activeMode() === 'limit') {
      const lp = Number(limitInput.value || 0);
      if (!lp || lp <= 0) errs.push('指値価格を入力してください。');
    }

    if (errs.length) {
      e.preventDefault();
      showErrors(errs);
      return;
    }

    showErrors([]);
  });
})();