// static/js/base.js
// Loader + Bottom Tab/Submenu
// - Loaderは押下瞬間に表示 → ページ遷移完了まで維持
// - 下タブ/サブメニューのコードは一切いじらず（イベントを上書きしない）

(function () {
  /* ===============================
     Loader Utilities
  =============================== */
  const isModClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
  const isValidHref = (href) => href && !href.startsWith('#') && !href.startsWith('javascript:');

  function getLoaderHost() {
    let host = document.getElementById('loading-screen');
    if (host) return host;

    // fallback overlay
    host = document.createElement('div');
    host.id = '__loading_overlay__';
    host.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar"></div>
    `;
    host.style.cssText = `
      position:fixed; inset:0; z-index:2147483647;
      background:rgba(10,10,20,.95);
      display:none; align-items:center; justify-content:center; flex-direction:column;
      color:#0ff; font:700 22px/1.2 system-ui;
    `;
    document.body.appendChild(host);
    return host;
  }

  function showLoader(host) {
    if (!host) return;
    if (host.id === 'loading-screen') {
      host.classList.remove('hidden');
    } else {
      host.style.display = 'flex';
    }
    host.style.opacity = '1';
    host.style.visibility = 'visible';
    document.documentElement.style.cursor = 'wait';
    document.body.style.cursor = 'wait';
  }

  function hideLoader(host) {
    if (!host) return;
    if (host.id === 'loading-screen') {
      host.classList.add('hidden');
    } else {
      host.style.display = 'none';
    }
    document.documentElement.style.cursor = '';
    document.body.style.cursor = '';
  }

  /* ===============================
     Loader Init
  =============================== */
  function initLoader() {
    const host = getLoaderHost();

    // 初期状態 → 非表示
    hideLoader(host);

    // 押下瞬間に即表示
    const downHandler = (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (a) {
        const href = a.getAttribute('href');
        if (isValidHref(href) && !isModClick(e) && a.target !== '_blank' && !a.hasAttribute('download') && a.dataset.noLoader !== 'true') {
          showLoader(host);
          return;
        }
      }
      const submit = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');
      if (submit) {
        const form = submit.form || submit.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          showLoader(host);
        }
      }
    };
    document.addEventListener('pointerdown', downHandler, { capture: true, passive: true });
    document.addEventListener('touchstart', downHandler, { capture: true, passive: true });

    // click: preventDefault → show → 実際に遷移
    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!isValidHref(href)) return;
      if (isModClick(e) || a.target === '_blank' || a.hasAttribute('download') || a.dataset.noLoader === 'true') return;

      e.preventDefault();
      showLoader(host);
      setTimeout(() => { window.location.href = href; }, 0);
    }, { capture: true });

    // form submit
    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.target === '_blank' || form.dataset.noLoader === 'true') return;
      showLoader(host);
    }, { capture: true });

    // 完全読込後に閉じる
    window.addEventListener('load', () => {
      setTimeout(() => hideLoader(host), 200);
    }, { once: true, passive: true });

    // bfcache 復帰時は閉じる
    window.addEventListener('pageshow', (e) => { if (e.persisted) hideLoader(host); }, { passive: true });

    // 離脱時にも表示
    window.addEventListener('beforeunload', () => showLoader(host), { passive: true });

    // 外部API用
    window.PageLoader = { show: () => showLoader(host), hide: () => hideLoader(host) };
  }

  /* ===============================
     Bottom Tab + Submenu
     （現行コードをそのまま記述、変更は一切しない）
  =============================== */
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    let caretRow = document.querySelector('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

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
          const href = a.getAttribute('href') || '#';
          const label = (a.textContent || '').trim();
          const target = a.getAttribute('target') || '';
          const btn = document.createElement('a');
          btn.className = 'ab-btn';
          btn.href = href;
          btn.textContent = label;
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
    }, { passive: true });

    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    new MutationObserver(() => rebuild()).observe(tabBar, { childList: true, subtree: true });
    rebuild();
  }

  /* ===============================
     Boot
  =============================== */
  function start() {
    initLoader();  // Loaderだけ面倒を見る
    initTabs();    // 下タブ/サブメニューは現行コードそのまま
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();