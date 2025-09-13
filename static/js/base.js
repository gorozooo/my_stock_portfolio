// static/js/base.js
// Loader + Bottom Tab/Submenu combined

(function () {
  /* =========================================
     1) Loader
  ========================================= */
  function initLoader() {
    let host = document.querySelector('#loading-screen');
    let mode = 'screen';

    if (!host) {
      // fallback overlay
      mode = 'overlay';
      const style = document.createElement('style');
      style.textContent = `
        #loading-overlay{
          position:fixed;inset:0;z-index:2147483647;
          background:rgba(10,10,20,.95);
          display:flex;align-items:center;justify-content:center;flex-direction:column;
          opacity:1;transition:opacity .22s ease;
        }
        #loading-overlay .loading-text{
          color:#0ff;font:700 22px/1.2 "Orbitron",system-ui;
          text-shadow:0 0 10px #0ff,0 0 20px #0ff;
        }
        #loading-overlay .loading-bar{
          width:220px;height:6px;border-radius:4px;margin-top:12px;
          background:linear-gradient(90deg,#0ff,#f0f,#0ff);
          background-size:200% 100%;
          animation:loadslide 2s linear infinite;
        }
        @keyframes loadslide{0%{background-position:0 0}100%{background-position:200% 0}}
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
      host.style.display = 'flex';
      host.style.opacity = '1';
      host.style.zIndex = '2147483647';
      document.documentElement.style.cursor = 'wait';
      document.body.style.cursor = 'wait';
      if (typeof cb === 'function') {
        requestAnimationFrame(cb);
      }
    }

    function hide() {
      host.style.opacity = '0';
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') {
          host.style.display = 'none';
          document.documentElement.style.cursor = '';
          document.body.style.cursor = '';
        }
      }, 240);
    }

    window.__loader = { show, hide };

    // 初回：必ず表示 → loadで消す
    show();
    window.addEventListener('load', hide, { passive: true });
    window.addEventListener('beforeunload', () => show(), { passive: true });
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });
  }

  /* =========================================
     1.5) Instant-Show Hook
  ========================================= */
  function initInstantHook() {
    function isModified(e) {
      return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
    }
    function validAnchor(a) {
      if (!a) return false;
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
      if (a.target === '_blank' || a.hasAttribute('download') || a.dataset.noLoader === 'true') return false;
      return true;
    }

    document.addEventListener('pointerdown', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (a && validAnchor(a) && !isModified(e)) window.__loader?.show();
    }, { capture: true, passive: true });

    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a || !validAnchor(a) || isModified(e)) return;
      e.preventDefault();
      window.__loader?.show(() => (window.location.href = a.href));
    }, { capture: true });

    document.addEventListener('submit', (e) => {
      const f = e.target;
      if (!(f instanceof HTMLFormElement)) return;
      if (f.target === '_blank' || f.dataset.noLoader === 'true') return;
      window.__loader?.show();
    }, { capture: true });
  }

  /* =========================================
     2) Bottom Tab + Submenu
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

        tab.querySelectorAll('.tab-caret,.caret,.caret-icon,[data-caret],[data-role="caret"]').forEach(n => n.remove());

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
          cell.appendChild(document.createElement('div')).className = 'caret-placeholder';
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
          btn.addEventListener('click', (e) => {
            if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
            e.preventDefault();
            window.__loader?.show(() => (window.location.href = href));
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
      setTimeout(() => { if (!actionbar.classList.contains('show')) actionbar.style.display = 'none'; }, 160);
      openKey = null;
    }

    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar = !!e.target.closest('.tab-actionbar');
      const inRow = !!e.target.closest('.caret-row');
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
    initInstantHook();
    initTabs();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();