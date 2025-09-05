// カードクリックで詳細モーダルを開く（リンクはそのまま生かす）
document.addEventListener('DOMContentLoaded', () => {
  const container = document.querySelector('.portfolio-container') || document;

  // クリック委譲：.stock-card 内部の a は通常遷移、カード本体クリックでモーダル
  container.addEventListener('click', (e) => {
    const card = e.target.closest('.stock-card');
    if (!card) return;

    // a要素を直接クリックした場合は通常リンクを優先
    const anchor = e.target.closest('a');
    if (anchor) return;

    e.preventDefault();

    // dataset から安全に読み出し
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

    if (!payload.id || payload.id === '0') {
      // 念のためIDがないカードは無視（404防止）
      return;
    }

    // 新モーダルを開く
    if (window.__DETAIL_MODAL__ && typeof window.__DETAIL_MODAL__.open === 'function') {
      window.__DETAIL_MODAL__.open(payload);
    }
  });

  // キーボード操作（Enter/Spaceで開く）
  container.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.stock-card');
    if (!card) return;
    e.preventDefault();

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
  });
});