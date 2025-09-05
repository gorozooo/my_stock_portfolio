(() => {
  // ====== modal DOM をなければ動的生成 ======
  let modalEl = document.getElementById('detail-modal');
  if (!modalEl) {
    modalEl = document.createElement('div');
    modalEl.id = 'detail-modal';
    modalEl.innerHTML = `
      <div class="modal-backdrop" data-modal-close></div>
      <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="detail-title">
        <div class="modal-header">
          <div class="title-wrap">
            <span id="detail-title">—</span>
            <span class="code-chip" id="detail-code">—</span>
          </div>
          <button class="close-btn" data-modal-close aria-label="閉じる">×</button>
        </div>
        <div class="modal-body" data-modal-body>
          <!-- ここにJSで中身を注入 -->
        </div>
        <div class="modal-footer">
          <a id="detail-edit-link" class="btn">編集ページへ</a>
          <a id="detail-sell-link" class="btn danger">売却ページへ</a>
        </div>
      </div>
    `;
    document.body.appendChild(modalEl);
  }

  // ====== スクロールロック（横ズレ無し） ======
  let __scrollY = 0;
  function lockBodyScroll() {
    if (document.body.classList.contains('modal-open')) return;
    __scrollY = window.scrollY || window.pageYOffset || 0;
    const sbw = window.innerWidth - document.documentElement.clientWidth;
    if (sbw > 0) {
      document.documentElement.style.setProperty('--scrollbar-w', sbw + 'px');
      document.body.classList.add('has-scrollbar-padding');
    }
    document.body.classList.add('modal-open');
    document.body.style.top = `-${__scrollY}px`;
  }
  function unlockBodyScroll() {
    document.body.classList.remove('modal-open', 'has-scrollbar-padding');
    document.body.style.top = '';
    window.scrollTo(0, __scrollY || 0);
    __scrollY = 0;
  }

  // ====== 公開API：モーダルを開く ======
  function openDetailModal(payload) {
    // payload: { id, name, ticker, shares, unit_price, current_price, profit_amount, profit_rate, account, broker }
    const titleEl = modalEl.querySelector('#detail-title');
    const codeEl = modalEl.querySelector('#detail-code');
    const bodyEl = modalEl.querySelector('[data-modal-body]');
    const editLink = modalEl.querySelector('#detail-edit-link');
    const sellLink = modalEl.querySelector('#detail-sell-link');

    const id = payload.id || '';
    const name = payload.name || '—';
    const ticker = payload.ticker || '—';
    const shares = toInt(payload.shares);
    const unit = toNum(payload.unit_price);
    const cur = toNum(payload.current_price) || unit;
    const pl = toNum(payload.profit_amount);
    const pr = toNum(payload.profit_rate);
    const account = payload.account || '—';
    const broker = payload.broker || '—';

    const marketValue = shares * cur;
    const totalCost = shares * unit;
    const profit = (marketValue - totalCost);

    // 初回フレームでの点滅抑止
    modalEl.classList.add('is-opening');

    // タイトル
    titleEl.textContent = name;
    codeEl.textContent = ticker;

    // 本文（軽量な要約 + 明細）
    bodyEl.innerHTML = `
      <div class="pill-group">
        <div class="pill"><span class="k">保有株数</span><span class="v">${fmtInt(shares)} 株</span></div>
        <div class="pill"><span class="k">取得単価</span><span class="v">¥${fmtInt(unit)}</span></div>
        <div class="pill"><span class="k">現在株価</span><span class="v">¥${fmtInt(cur)}</span></div>
        <div class="pill"><span class="k">口座</span><span class="v">${escapeHTML(account)}</span></div>
        <div class="pill"><span class="k">証券</span><span class="v">${escapeHTML(broker)}</span></div>
      </div>

      <div class="row"><span class="k">取得額</span><span class="v">¥${fmtInt(totalCost)}</span></div>
      <div class="row"><span class="k">評価額</span><span class="v">¥${fmtInt(marketValue)}</span></div>
      <div class="row">
        <span class="k">損益</span>
        <span class="v ${profit >= 0 ? 'profit-pos':'profit-neg'}">¥${fmtInt(profit)}${pr !== null ? ` (${fmtNum(pr)}%)` : ''}</span>
      </div>
    `;

    // 専用ページリンク
    if (id) {
      editLink.href = `/stocks/${id}/edit/`;
      sellLink.href = `/stocks/${id}/sell/`;
      editLink.setAttribute('data-stock-id', id);
      sellLink.setAttribute('data-stock-id', id);
    } else {
      // 念のため無効化
      editLink.removeAttribute('href');
      sellLink.removeAttribute('href');
    }

    // ロック → 表示
    lockBodyScroll();
    modalEl.classList.add('is-open');

    // 次フレームで opening 解除（以降フェード有効）
    requestAnimationFrame(() => modalEl.classList.remove('is-opening'));
  }

  function closeDetailModal() {
    modalEl.classList.remove('is-open');
    unlockBodyScroll();
  }

  // 背景クリック/×/Esc で閉じる
  modalEl.addEventListener('click', (e) => {
    if (e.target === modalEl || e.target.hasAttribute('data-modal-close')) {
      closeDetailModal();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalEl.classList.contains('is-open')) closeDetailModal();
  });

  // ====== util ======
  function toNum(v){ const n = Number(v); return Number.isFinite(n) ? n : 0; }
  function toInt(v){ const n = parseInt(v, 10); return Number.isFinite(n) ? n : 0; }
  function fmtInt(n){ return (Math.round(n)).toLocaleString(); }
  function fmtNum(n){ return Number(n).toLocaleString(undefined, {maximumFractionDigits:2}); }
  function escapeHTML(str){
    return String(str ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  }

  // グローバル公開（リンク側から呼べるように）
  window.__DETAIL_MODAL__ = { open: openDetailModal, close: closeDetailModal };
})();