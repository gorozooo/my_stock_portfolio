/* stock_list.modal-links.js  (revert: ブロック処理なしのシンプル版)
   - カードをタップ/Enter/Space で新しい詳細モーダル(window.__DETAIL_MODAL__)を開く
   - 旧モーダル(#stock-modal)には干渉しない（削除・監視・横取りしない）
*/
(function () {
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
    if (!payload.id || payload.id === '0') return;
    if (window.__DETAIL_MODAL__ && typeof window.__DETAIL_MODAL__.open === 'function') {
      window.__DETAIL_MODAL__.open(payload);
    }
  }

  const root = document.querySelector('.portfolio-container') || document;

  // クリック（バブリング段階・横取りしない）
  root.addEventListener('click', (e) => {
    const card = e.target.closest?.('.stock-card');
    if (!card) return;

    // aタグ（編集/売却リンク）は通常遷移
    if (e.target.closest?.('a')) return;

    openNewModalFromCard(card);
    // ※ preventDefault/stopPropagation はしない → 旧ハンドラにも干渉しない
  });

  // キー操作（Enter/Space）
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest?.('.stock-card');
    if (!card) return;

    // aフォーカス時は無視
    if (document.activeElement?.closest?.('a')) return;

    openNewModalFromCard(card);
    // ※ キーも横取りしない
  });
})();