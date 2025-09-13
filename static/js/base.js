// static/js/base.js
// Loader + Bottom Tab/Submenu combined
// - Instant show on pointerdown/touchstart
// - Keeps visible until navigation truly starts (click->preventDefault + href遷移)
// - Uses #loading-screen if present, otherwise creates top-most overlay
// - Bottom tab/submenu: current behavior kept; navigation goes via loader

(function () {
  /* =========================================
     0) Small helpers
  ========================================= */
  const isModClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
  const isValidHref = (href) =>
    href && !href.startsWith('#') && !href.startsWith('javascript:');

  /* =========================================
     1) Loader
  ========================================= */
  function initLoader() {
    // 1-1. host を決定：既存 (#loading-screen) があれば利用、なければ overlay を作成
    let host = document.querySelector('#loading-screen');
    let useOverlay = false;

    if (!host) {
      useOverlay = true;

      // 強制力のあるCSS（!important）を注入（他CSSに上書きされないように）
      const style = document.createElement('style');
      style.id = '__hardloader_css__';
      style.textContent = `
        #__hardloader__ {
          position: fixed !important;
          inset: 0 !important;
          z-index: 2147483647 !important;
          background: rgba(10,10,20,.95) !important;
          display: none !important;
          opacity: 0 !important;
          transition: opacity .22s ease !important;
          display: flex !important;
          align-items: center !important;
          justify-content: center !important;
          flex-direction: column !important;
          pointer-events: all !important;
        }
        #__hardloader__ .loading-text {
          color: #0ff !important;
          font: 700 22px/1.2 "Orbitron", system-ui !important;
          text-shadow: 0 0 10px #0ff, 0 0 20px #0ff !important;
        }
        #__hardloader__ .loading-bar {
          width: 220px !important;
          height: 6px !important;
          border-radius: 4px !important;
          margin-top: 12px !important;
          background: linear-gradient(90deg, #0ff, #f0f, #0ff) !important;
          background-size: 200% 100% !important;
          animation: __hardloader_slide 2s linear infinite !important;
        }
        @keyframes __hardloader_slide {
          0% { background-position: 0 0 }
          100% { background-position: 200% 0 }
        }
      `;
      document.head.appendChild(style);

      host = document.createElement('div');
      host.id = '__hardloader__';
      host.innerHTML = `
        <div class="loading-text">Now Loading…</div>
        <div class="loading-bar"></div>
      `;
      // bodyがまだないケースはほぼ無いけど、保険で待機
      const append = () => (document.body ? document.body.appendChild(host) : setTimeout(append, 0));
      append();
    }

    // 1-2. API
    function show(cb) {
      // 直書きで最優先の display/opacity/z-index を付与
      host.style.setProperty('display', 'flex', 'important');
      // reflow で描画フレーム確保
      // eslint-disable-next-line no-unused-expressions
      host.offsetHeight;
      host.style.setProperty('opacity', '1', 'important');
      host.style.setProperty('z-index', '2147483647', 'important');
      document.documentElement.style.cursor = 'wait';
      document.body.style.cursor = 'wait';
      if (typeof cb === 'function') cb();
    }

    function hide() {
      host.style.setProperty('opacity', '0', 'important');
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') {
          host.style.setProperty('display', 'none', 'important');
          document.documentElement.style.cursor = '';
          document.body.style.cursor = '';
        }
      }, 250);
    }

    window.__loader = { show, hide };

    // 1-3. “前の挙動”：
    // 初回は必ず表示 → window.load で閉じる
    show();
    window.addEventListener('load', hide, { passive: true });
    // 離脱時にも必ず表示
    window.addEventListener('beforeunload', () => show(), { passive: true });
    // bfcache 復帰時は不要
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });
  }

  /* =========================================
     1.5) Instant-Show Hook（押下の瞬間に出す＆維持）
  ========================================= */
  function initInstantHook() {
    // pointerdown/touchstart で最速表示（実際の遷移は click で上書き）
    const downHandler = (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      // フォーム送信ボタンなども拾う
      const submitBtn = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');

      if (a) {
        if (!isModClick(e) && isValidHref(a.getAttribute('href')) && a.target !== '_blank' && !a.hasAttribute('download') && a.dataset.noLoader !== 'true') {
          window.__loader?.show();
        }
      } else if (submitBtn) {
        const form = submitBtn.form || submitBtn.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          window.__loader?.show();
        }
      }
    };
    document.addEventListener('pointerdown', downHandler, { capture: true, passive: true });
    document.addEventListener('touchstart', downHandler, { capture: true, passive: true });

    // click は必ず preventDefault して loader.show→href へ（維持させるため）
    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;

      const href = a.getAttribute('href');
      if (!isValidHref(href)) return;
      if (a.target === '_blank' || a.hasAttribute('download') || a.dataset.noLoader === 'true') return;
      if (isModClick(e)) return;

      e.preventDefault();
      window.__loader?.show(() => { window.location.href = href; });
    }, { capture: true });

    // フォーム submit
    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.target === '_blank' || form.dataset.noLoader === 'true') return;
      // ここで出しっぱなしにして、実際の遷移はブラウザに任せる
      window.__loader?.show();
    }, { capture: true });
  }

  /* =========================================
     2) Bottom Tab + Submenu（現状の動作は維持）
  ========================================= */
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    // ケアレット用行
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

        // 既存の飾りケアレットは除去
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

      // タブ本体のクリックは“通常遷移”。ナビは loader 経由（瞬時表示）
      map.forEach(({ link }) => {
        if (!link) return;
        link.addEventListener('click', (e) => {
          const href = link.getAttribute('href');
          const target = link.getAttribute('target') || '';
          if (!isValidHref(href) || target === '_blank') return;
          e.preventDefault();
          window.__loader?.show(() => { window.location.href = href; });
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
            if (!isValidHref(href) || target === '_blank') return;
            e.preventDefault();
            window.__loader?.show(() => { window.location.href = href; });
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
    }, { passive: true });

    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    new MutationObserver(() => rebuild()).observe(tabBar, { childList: true, subtree: true });
    rebuild();
  }

  /* =========================================
     Boot
  ========================================= */
  function start() {
    initLoader();
    initInstantHook();  // ← 押下の瞬間に出す
    initTabs();         // ← 下タブ/サブメニュー維持
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();