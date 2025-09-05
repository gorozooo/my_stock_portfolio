// モーダル内の「編集/売却」リンクを、クリックされたカードの data-id で動的に設定。
// 初期 href は "#" なので、セット前に遷移する事故を防止。

(function () {
  document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('stock-modal');
    if (!modal) return;

    const editLink = document.getElementById('modal-edit-link');
    const sellLink = document.getElementById('modal-sell-link');

    const editTpl = modal.dataset.editUrlTemplate || '/stocks/{id}/edit/';
    const sellTpl = modal.dataset.sellUrlTemplate || '/stocks/{id}/sell/';

    let lastCardId = null;

    const makeHref = (tpl, id) => String(tpl).replace('{id}', String(id));

    const setLinks = (id) => {
      lastCardId = id;
      editLink.setAttribute('href', makeHref(editTpl, id));
      sellLink.setAttribute('href', makeHref(sellTpl, id));
    };

    // カードからIDを受け取り、モーダルにリンクを差し込む
    document.querySelectorAll('.stock-card').forEach(card => {
      card.addEventListener('click', (e) => {
        // スワイプボタン領域のクリックは除外
        if (e.target.closest('.card-actions')) return;
        const id = card.dataset.id;
        if (id) setLinks(id);
      });

      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          const id = card.dataset.id;
          if (id) setLinks(id);
        }
      });
    });

    // モーダルが開いた後でも、リンクが未設定なら補完
    const ensureLinks = () => {
      if (!lastCardId) return;
      if (editLink.getAttribute('href') === '#' || !editLink.getAttribute('href')) {
        setLinks(lastCardId);
      }
    };
    const mo = new MutationObserver(ensureLinks);
    mo.observe(modal, { attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] });

    // 念のため、リンククリック時に未設定なら遷移をブロック
    const guard = (ev) => {
      const href = ev.currentTarget.getAttribute('href') || '#';
      if (href === '#' || href.includes('{id}')) {
        ev.preventDefault();
        // 直近のカードIDが取れていればその場で差し込み
        if (lastCardId) {
          setLinks(lastCardId);
          // 再クリックで遷移できるようにする（自動遷移したければここで location.assign(href) でもOK）
        }
      }
    };
    editLink.addEventListener('click', guard);
    sellLink.addEventListener('click', guard);
  });
})();