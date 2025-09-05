/* 旧モーダル封じ + 旧クリックを横取りするパッチ */
(function () {
  // 旧モーダルを見つけたら即除去
  function nukeLegacyModal(root = document) {
    const m = root.getElementById ? root.getElementById('stock-modal') : root.querySelector?.('#stock-modal');
    if (m) m.remove();
  }

  // 初回＆DOMContentLoaded後に除去
  nukeLegacyModal(document);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => nukeLegacyModal(document), { once: true });
  }

  // DOMに後から挿入されても即除去
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes?.forEach((n) => {
        if (!(n instanceof HTMLElement)) return;
        if (n.id === 'stock-modal') {
          n.remove();
        } else {
          const found = n.querySelector?.('#stock-modal');
          if (found) found.remove();
        }
      });
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });

  // 旧コードのクリックを“キャプチャ段階”で横取り（カード本体タップ＝新モーダル）
  function openNewModalFromCard(card) {
    if (!card) return;
    const data = {
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
    if (!data.id || data.id === '0') return; // 404防止
    if (window.__DETAIL_MODAL__?.open) window.__DETAIL_MODAL__.open(data);
  }

  const root = document.querySelector('.portfolio-container') || document;

  // タップ/クリックを横取り
  root.addEventListener(
    'click',
    (e) => {
      const card = e.target.closest?.('.stock-card');
      if (!card) return;

      // 編集/売却の <a> クリックは通常遷移
      if (e.target.closest?.('a')) return;

      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      openNewModalFromCard(card);
    },
    true // capture
  );

  // Enter/Space キー操作も横取り
  root.addEventListener(
    'keydown',
    (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const card = e.target.closest?.('.stock-card');
      if (!card) return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      openNewModalFromCard(card);
    },
    true
  );

  // ページ離脱で監視停止
  window.addEventListener('beforeunload', () => mo.disconnect(), { once: true });
})();