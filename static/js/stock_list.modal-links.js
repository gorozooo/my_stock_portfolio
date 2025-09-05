// モーダル内「編集ページへ」「売却ページへ」の href を、クリックしたカードIDで生成して差し込む。
// 既存の stock_list.js がモーダルの開閉・本文描画を担当していても干渉しない設計。

(function () {
  document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('stock-modal');
    if (!modal) return;

    const editLink = document.getElementById('modal-edit-link');
    const sellLink = document.getElementById('modal-sell-link');

    // DjangoでダミーID=0のURLをテンプレートとして埋め込み済み
    const editTpl = modal.dataset.editUrlTemplate || '';
    const sellTpl = modal.dataset.sellUrlTemplate || '';

    let lastCardId = null;

    const toHref = (tpl, realId) => {
      if (!tpl) return '#';
      if (tpl.endsWith('/0/')) return tpl.replace(/\/0\/$/, `/${realId}/`);
      if (tpl.endsWith('/0'))  return tpl.replace(/\/0$/,  `/${realId}`);
      return tpl.replace(/0(?=\/?$)/, String(realId)); // 念のため
    };

    const setLinks = (id) => {
      if (!id) return;
      editLink.href = toHref(editTpl, id);
      sellLink.href = toHref(sellTpl, id);
    };

    // カードクリックで ID を保持 & リンク差し替え
    document.querySelectorAll('.stock-card').forEach(card => {
      card.addEventListener('click', (e) => {
        // スワイプアクション領域からのクリックは無視
        if (e.target.closest('.card-actions')) return;
        lastCardId = card.dataset.id;
        setLinks(lastCardId);
      });

      // キーボード操作対応
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          lastCardId = card.dataset.id;
          setLinks(lastCardId);
        }
      });
    });

    // モーダルが表示状態になった際にも、未設定なら補完
    const onModalShown = () => {
      if (!lastCardId) return;
      if (!editLink.getAttribute('href') || editLink.getAttribute('href') === '#') {
        setLinks(lastCardId);
      }
    };

    const mo = new MutationObserver(onModalShown);
    mo.observe(modal, { attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] });

    // 安全のための閉じる動作（既存と二重でも悪さしない）
    modal.addEventListener('click', (e) => {
      if (e.target === modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
      }
    });
    modal.querySelector('.modal-close')?.addEventListener('click', () => {
      modal.style.display = 'none';
      modal.setAttribute('aria-hidden', 'true');
    });
  });
})();