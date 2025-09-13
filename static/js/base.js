// static/js/base.js
// 下タブ / サブメニューのみ（ローダーは一切生成しない）
// 遷移時は window.PageLoader?.show() を呼ぶだけ

(function () {
  const isValidHref = (href) => href && !href.startsWith('#') && !href.startsWith('javascript:');

  // ---- 「通常のリンク遷移」でもローダーを出す（存在すれば）
  // （loader.jsが後読みでも、ハンドラは先に仕掛けてOK。呼ぶ瞬間に存在チェック）
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;

    const href   = a.getAttribute('href');
    const target = a.getAttribute('target') || '';
    const dl     = a.hasAttribute('download');

    if (!isValidHref(href) || target === '_blank' || dl || a.dataset.noLoader === 'true') return;

    // すでに他で prevent されているなら触らない
    if (e.defaultPrevented) return;

    // 画面遷移の直前にローダー（あれば）
    if (window.PageLoader && typeof window.PageLoader.show === 'function') {
      e.preventDefault();
      window.PageLoader.show();
      // 遅延0で即遷移（描画フレームを1つ確保）
      setTimeout(() => { window.location.href = href; }, 0);
    }
  }, { capture: true, passive: false });

  // ---- フォーム送信時もローダー（存在すれば）
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.getAttribute('target') === '_blank' || form.dataset.noLoader === 'true') return;
    if (window.PageLoader && typeof window.PageLoader.show === 'function') {
      window.PageLoader.show();
    }
  }, { capture: true });

  // ===== ここから下は「下タブ / サブメニュー」だけ =====
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    // ケアレット用の行（下タブ直下）
    let caretRow = document.querySelector('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

    // サブメニューのアクションバー
    let actionbar = document.querySelector('.tab-actionbar');
    if (!actionbar) {
      actionbar = document.createElement('div');
      actionbar.className = 'tab-actionbar';
      document.body.appendChild(actionbar);
    }

    let openKey = null;
    const map = new Map();

    function rebuild() {
      caretRow.innerHTML = '';
      map.clear();

      const tabs = Array.from(tabBar.querySelectorAll('.tab-item'));
      let seq = 0;

      tabs.forEach((tab) => {
        let key = tab.dataset.tabkey;
        if (!key) {
          key = `t${Date.now().toString(36)}_${(seq++).toString(36)}`;
          tab.dataset.tabkey = key;
        }

        // 既存の飾りケアレットを削除（重複回避）
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(n => n.remove());

        const link    = tab.querySelector('.tab-link');
        const submenu = tab.querySelector('.sub-menu');

        const cell = document.createElement('div');
        cell.className = 'caret-cell';

        let caretBtn = null;
        if (submenu) {
          caretBtn = document.createElement('button');
          caretBtn.type = 'button';
          caretBtn.className = 'caret-btn';
          caretBtn.textContent = '▾';
          caretBtn.setAttribute('aria-expanded', 'false');
          caretBtn.dataset.tabkey = key;
          cell.appendChild(caretBtn);
        } else {
          const ph = document.createElement('div');
          ph.className = 'caret-placeholder';
          cell.appendChild(ph);
        }
        caretRow.appendChild(cell);

        map.set(key, { tab, link, submenu, caretBtn });
      });

      // ケアレットで開閉
      map.forEach(({ caretBtn }, key) => {
        if (!caretBtn) return;
        caretBtn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (openKey === key) hideBar();
          else showBar(key);
        };
      });

      if (openKey && !map.has(openKey)) hideBar();
    }

    function showBar(key) {
      const rec = map.get(key);
      if (!rec || !rec.submenu) return;

      actionbar.innerHTML = '';
      const links = rec.submenu.querySelectorAll('a');

      if (!links.length) {
        const none = document.createElement('span');
        none.className = 'ab-btn';
        none.textContent = 'メニューなし';
        actionbar.appendChild(none);
      } else {
        links.forEach((a) => {
          const href   = a.getAttribute('href') || '#';
          const label  = (a.textContent || '').trim();
          const target = a.getAttribute('target') || '';

          const btn = document.createElement('a');
          btn.className = 'ab-btn';
          btn.href = href;
          btn.textContent = label;

          btn.addEventListener('click', (e) => {
            if (!isValidHref(href) || target === '_blank') return;
            e.preventDefault();
            if (window.PageLoader && typeof window.PageLoader.show === 'function') {
              window.PageLoader.show();
              setTimeout(() => { window.location.href = href; }, 0);
            } else {
              window.location.href = href;
            }
          });

          actionbar.appendChild(btn);
        });
      }

      // ケアレット状態更新
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      if (rec.caretBtn) rec.caretBtn.setAttribute('aria-expanded', 'true');

      actionbar.style.display = 'flex';
      requestAnimationFrame(() => actionbar.classList.add('show'));
      openKey = key;
    }

    function hideBar() {
      actionbar.classList.remove('show');
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      setTimeout(() => {
        if (!actionbar.classList.contains('show')) actionbar.style.display = 'none';
      }, 160);
      openKey = null;
    }

    // 外側クリック/ESC/リサイズで閉じる
    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar  = !!e.target.closest('.tab-actionbar');
      const inRow  = !!e.target.closest('.caret-row');
      const inTabs = !!e.target.closest('.bottom-tab');
      if (!inBar && !inRow && !inTabs) hideBar();
    }, { passive: true });
    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    new MutationObserver(() => rebuild()).observe(tabBar, { childList: true, subtree: true });
    rebuild();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTabs, { once: true });
  } else {
    initTabs();
  }
})();