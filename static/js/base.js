// static/js/base.js
// Loader + Bottom Tab & Submenu (all-in-one)
// ─ Loader: 「押した瞬間に表示→window.loadで閉じる」以前の挙動に戻す
// ─ Tabs  : caret-row と actionbar を使った仕組み（前回版を維持）
// ─ 追加  : 全ページ共通の「リンククリック／フォーム送信」最前面フックで即ローダー表示

(function () {
  /* =========================================
     1) Loader — “前の挙動”を完全再現
  ========================================= */
  function initLoader() {
    let host = document.querySelector('#loading-screen'); // テンプレ既存の派手版
    let mode = 'screen';
    if (!host) {
      // 簡易オーバーレイを自動生成
      mode = 'overlay';
      const style = document.createElement('style');
      style.id = 'loading-overlay-style';
      style.textContent = `
        #loading-overlay{
          position:fixed; inset:0; z-index:9999;
          background:rgba(10,10,20,.95);
          display:none; opacity:0; transition:opacity .22s ease;
          display:flex; align-items:center; justify-content:center; flex-direction:column
        }
        #loading-overlay .loading-text{
          color:#0ff; font:700 22px/1.2 "Orbitron",system-ui;
          text-shadow:0 0 10px #0ff,0 0 20px #0ff
        }
        #loading-overlay .loading-bar{
          width:220px; height:6px; border-radius:4px; margin-top:12px;
          background:linear-gradient(90deg,#0ff,#f0f,#0ff);
          background-size:200% 100%; animation:loadslide 2s linear infinite
        }
        @keyframes loadslide { 0%{background-position:0 0} 100%{background-position:200% 0} }
      `;
      document.head.appendChild(style);

      host = document.createElement('div');
      host.id = 'loading-overlay';
      host.innerHTML = `
        <div class="loading-text">Now Loading…</div>
        <div class="loading-bar"></div>
      `;
      document.body.appendChild(host);
    }

    function show(cb) {
      if (mode === 'screen') {
        const cs = getComputedStyle(host);
        if (cs.display === 'none') host.style.display = 'flex';
        host.style.opacity = '1';
      } else {
        host.style.display = 'flex';
        requestAnimationFrame(() => { host.style.opacity = '1'; });
      }
      if (typeof cb === 'function') setTimeout(cb, 0); // ←“押した瞬間”に見せたいので 0ms
    }

    function hide() {
      host.style.opacity = '0';
      const delay = (mode === 'screen') ? 220 : 200;
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') host.style.display = 'none';
      }, delay);
    }

    window.__loader = { show, hide };

    // 初回は必ず表示 → window.load で閉じる
    if (getComputedStyle(host).display === 'none') {
      show();
    } else {
      host.style.opacity = '1';
    }

    window.addEventListener('load', hide, { passive: true });
    window.addEventListener('beforeunload', () => show(), { passive: true });
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });

    // 任意ナビゲーション用
    window.__goto = function (href) {
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      show(() => { window.location.href = href; });
    };
  }

  /* =========================================
     1.5) “押した瞬間”にローダーを出すグローバルフック
     - a要素クリック（左クリック／修飾キーなし／_blank 以外）
     - form送信（target=_blank 以外）
     - data-no-loader 付きは除外
  ========================================= */
  function initGlobalNavHook() {
    const isModifiedClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;

    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;

      // 除外条件
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      if (a.getAttribute('target') === '_blank') return;
      if (a.hasAttribute('download')) return;
      if (a.dataset.noLoader === 'true') return;
      if (isModifiedClick(e)) return;

      // 同一オリジンでなくてもページ遷移ならOK（SPAでない想定）
      e.preventDefault();
      if (window.__loader) window.__loader.show(() => (window.location.href = href));
      else window.location.href = href;
    }, { capture: true }); // ← capture にして最前でフック

    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.getAttribute('target') === '_blank') return;
      if (form.dataset.noLoader === 'true') return;

      // 送信は止めずにローダーだけ表示
      if (window.__loader) window.__loader.show();
    }, { capture: true });
  }

  /* =========================================
     2) Bottom Tab + Submenu（前回版を維持）
     caret-row と actionbar を使った仕組み
  ========================================= */
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

      // タブ本体のクリックはグローバルフックが拾うので、ここでは不要
      if (openKey && !map.has(openKey)) hideBar();
    }

    function showBar(key) {
      const rec = map.get(key);
      if (!rec || !rec.submenu) return;

      actionbar.innerHTML = '';
      const links = rec.submenu.querySelectorAll('a');
      if (links.length === 0) {
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
          // ここもグローバルフックが拾うが、念のため
          btn.addEventListener('click', (e) => {
            if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
            e.preventDefault();
            if (window.__loader) window.__loader.show(() => (window.location.href = href));
            else window.location.href = href;
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

    const mo = new MutationObserver(() => rebuild());
    mo.observe(tabBar, { childList: true, subtree: true });

    rebuild();
  }

  /* =========================================
     起動
  ========================================= */
  function start() {
    initLoader();
    initGlobalNavHook(); // ← “押した瞬間”にローダー表示
    initTabs();          // ← 下タブ/サブメニュー（前回版を維持）
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();