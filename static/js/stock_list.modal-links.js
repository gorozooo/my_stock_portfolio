/* stock_list.modal-links.js
   - 新しい詳細モーダル（window.__DETAIL_MODAL__）へ誘導
   - 旧モーダル(#stock-modal)の出現を強制ブロック
   - クリックをキャプチャ段階で横取りして旧ハンドラを無効化
*/
(function () {
  // --- 旧モーダルの完全ブロック（CSSキルスイッチ + DOM除去 + 監視） ---
  function killLegacyModalOnce() {
    // 1) CSSで強制非表示（念のため）
    if (!document.getElementById('kill-legacy-modal-style')) {
      const style = document.createElement('style');
      style.id = 'kill-legacy-modal-style';
      style.textContent = `
        #stock-modal { display:none !important; visibility:hidden !important; opacity:0 !important; }
      `;
      document.head.appendChild(style);
    }
    // 2) 既に存在してたら即削除
    const legacy = document.getElementById('stock-modal');
    if (legacy) legacy.remove();
  }

  // 3) 後から挿入されても即削除（旧JSが動的生成するケース対策）
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes && m.addedNodes.forEach((n) => {
        if (!(n instanceof HTMLElement)) return;
        if (n.id === 'stock-modal') {
          n.remove();
        } else {
          const found = n.querySelector && n.querySelector('#stock-modal');
          if (found) found.remove();
        }
      });
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });

  // 初期キル
  killLegacyModalOnce();
  // DOMの準備が完了したらもう一度保険で実行
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', killLegacyModalOnce, { once: true });
  } else {
    killLegacyModalOnce();
  }

  // ====== 新モーダルを開く関数 ======
  function openNewModalFromCard(card) {
    if (!card) return;
    const payload = {
      id: card.dataset.id,
      name: card.dataset.name,
      ticker: card.dataset.ticker,
      shares: card.dataset.shares,
      unit_price: card.dataset.unit_price,
      current_price: card.dataset.current_price,
      profit_amount: card.dataset.profit,
      profit_rate: card.dataset.profit_rate,
      account: card.dataset.account,
      broker: card.dataset.broker,
    };
    if (!payload.id || payload.id === '0') return; // 404防止
    if (window.__DETAIL_MODAL__ && typeof window.__DETAIL_MODAL__.open === 'function') {
      window.__DETAIL_MODAL__.open(payload);
    }
  }

  // ====== クリックをキャプチャで横取り → 旧ハンドラ発火させない ======
  const root = document.querySelector('.portfolio-container') || document;

  root.addEventListener(
    'click',
    (e) => {
      const card = e.target.closest?.('.stock-card');
      if (!card) return;

      // aタグクリックは通常遷移（編集/売却ページへ）
      const anchor = e.target.closest('a');
      if (anchor) return;

      // ここからは「カード本体タップ＝新モーダル」
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();

      openNewModalFromCard(card);
    },
    /* capture */ true
  );

  // キーボード操作もキャプチャで横取り
  root.addEventListener(
    'keydown',
    (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const card = e.target.closest?.('.stock-card');
      if (!card) return;

      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();

      openNewModalFromCard(card);
    },
    /* capture */ true
  );

  // 念のため：ページ遷移前に監視停止
  window.addEventListener('beforeunload', () => mo.disconnect(), { once: true });
})();