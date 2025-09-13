// static/js/base.js
// Loader + 下タブ/サブメニュー
// ==========================================
// ・ローディング画面：押した瞬間から表示 → ページ遷移後に消える
// ・下タブ/サブメニュー：従来通りの動作を維持
// ==========================================

(function () {
  // ----------------------------
  // Loader
  // ----------------------------
  function forceShow(el) {
    if (!el) return;
    el.classList.remove('hidden');
    el.style.setProperty('display', 'flex', 'important');
    el.style.setProperty('opacity', '1', 'important');
    el.style.setProperty('visibility', 'visible', 'important');
    el.style.setProperty('pointer-events', 'auto', 'important');
    el.style.setProperty('z-index', '2147483647', 'important');
    document.documentElement.style.cursor = 'wait';
    document.body.style.cursor = 'wait';
  }

  function forceHide(el) {
    if (!el) return;
    el.classList.add('hidden');
    el.style.setProperty('opacity', '0', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.style.setProperty('pointer-events', 'none', 'important');
    setTimeout(() => {
      el.style.setProperty('display', 'none', 'important');
      document.documentElement.style.cursor = '';
      document.body.style.cursor = '';
    }, 350);
  }

  function getLoaderHost() {
    return document.getElementById('loading-screen');
  }

  function isValidAnchor(a, e) {
    if (!a) return false;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
    if (a.target === '_blank' || a.hasAttribute('download')) return false;
    if (a.dataset.noLoader === 'true') return false;
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)) return false;
    return true;
  }

  function initLoader() {
    const loader = getLoaderHost();
    if (!loader) return;

    // 初期は表示 → 読込完了で閉じる
    forceShow(loader);
    const closeAfterLoad = () => setTimeout(() => forceHide(loader), 250);
    if (document.readyState === 'complete') {
      closeAfterLoad();
    } else {
      window.addEventListener('load', closeAfterLoad, { once: true, passive: true });
    }

    // bfcache 復帰やエラーでも閉じる
    window.addEventListener('pageshow', (e) => { if (e.persisted) forceHide(loader); });
    window.addEventListener('error', () => forceHide(loader));
    window.addEventListener('unhandledrejection', () => forceHide(loader));

    // 押下した瞬間に表示
    const onPointerDown = (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (isValidAnchor(a, e)) {
        forceShow(loader);
        return;
      }
      const submit = e.target.closest &&
        e.target.closest('button[type="submit"], input[type="submit"]');
      if (submit) {
        const form = submit.form || submit.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          forceShow(loader);
        }
      }
    };
    document.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true });
    document.addEventListener('touchstart', onPointerDown, { capture: true, passive: true });

    // 離脱時に表示（Safari 対策）
    window.addEventListener('beforeunload', () => forceShow(loader), { passive: true });

    // 外部API
    window.PageLoader = {
      show: () => forceShow(loader),
      hide: () => forceHide(loader)
    };
  }

  // ----------------------------
  // 下タブ / サブメニュー
  // ----------------------------
  // ⚠️ ここから下タブ/サブメニューの処理です
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    // ケアレット行
    let caretRow = document.querySelector('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

    // サブメニュー用アクションバー
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

        // 既存のケアレットは削除
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(n => n.remove());

        const link = tab.querySelector('.tab-link');
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

      // ケアレット開閉
      map.forEach(({ caretBtn }, key) => {
        if (!caretBtn) return;
        caretBtn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (openKey === key) hideBar();
          else showBar(key);
        };
      });

      // タブクリック → ローディング経由で遷移
      map.forEach(({ link }) => {
        if (!link) return;
        link.addEventListener('click', (e) => {
          const href = link.getAttribute('href');
          const target = link.getAttribute('target') || '';
          if (!href || href.startsWith('#') || target === '_blank') return;
          e.preventDefault();
          window.PageLoader?.show();
          window.location.href = href;
        });
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
          const href = a.getAttribute('href') || '#';
          const label = (a.textContent || '').trim();
          const target = a.getAttribute('target') || '';
          const btn = document.createElement('a');
          btn.className = 'ab-btn';
          btn.href = href;
          btn.textContent = label;
          btn.addEventListener('click', (e) => {
            if (!href || href.startsWith('#') || target === '_blank') return;
            e.preventDefault();
            window.PageLoader?.show();
            window.location.href = href;
          });
          actionbar.appendChild(btn);
        });
      }

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

    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar  = !!e.target.closest('.tab-actionbar');
      const inRow  = !!e.target.closest('.caret-row');
      const inTabs = !!e.target.closest('.bottom-tab');
      if (!inBar && !inRow && !inTabs) hideBar();
    });

    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); });
    window.addEventListener('resize', hideBar);

    new MutationObserver(() => rebuild()).observe(tabBar, { childList: true, subtree: true });
    rebuild();
  }

  // ----------------------------
  // Boot
  // ----------------------------
  function start() {
    initLoader();
    initTabs(); // ← 下タブ/サブメニューもここで有効化
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();