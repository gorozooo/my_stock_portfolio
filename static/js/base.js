// static/js/base.js
// Loader + Bottom Tab & Submenu (unchanged) — instant show now truly sticks until navigation

(function () {
  /* =========================================
     1) Loader — shared
  ========================================= */
  function initLoader() {
    let host = document.querySelector('#loading-screen'); // fancy template screen
    let mode = 'screen';
    if (!host) {
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
      // show immediately and keep visible
      if (mode === 'screen') {
        if (getComputedStyle(host).display === 'none') host.style.display = 'flex';
        host.style.opacity = '1';
      } else {
        host.style.display = 'flex';
        // force reflow for immediate paint
        // eslint-disable-next-line no-unused-expressions
        host.offsetHeight;
        host.style.opacity = '1';
      }
      document.documentElement.style.cursor = 'wait';
      document.body.style.cursor = 'wait';
      if (typeof cb === 'function') setTimeout(cb, 0);
    }

    function hide() {
      host.style.opacity = '0';
      const delay = (mode === 'screen') ? 220 : 200;
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') {
          host.style.display = 'none';
          document.documentElement.style.cursor = '';
          document.body.style.cursor = '';
        }
      }, delay);
    }

    window.__loader = { show, hide };

    // initial: always visible until load (previous behavior)
    if (getComputedStyle(host).display === 'none') {
      show();
    } else {
      host.style.opacity = '1';
    }

    window.addEventListener('load', hide, { passive: true });
    window.addEventListener('beforeunload', () => show(), { passive: true });
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });

    // manual goto helper
    window.__goto = function (href) {
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      show(() => { window.location.href = href; });
    };
  }

  /* =========================================
     1.5) Instant-Show Hook — keep shown until nav actually starts
  ========================================= */
  function initInstantHook() {
    const isModifiedClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
    let pendingHideTimer = null;
    let navIntentArmed = false;

    function armIntent() {
      navIntentArmed = true;
      // keep loader visible long enough so “beforeunload” can fire
      if (pendingHideTimer) clearTimeout(pendingHideTimer);
      pendingHideTimer = setTimeout(() => {
        // if nothing happened (no beforeunload, no click handler navigation), hide it
        if (navIntentArmed && window.__loader) window.__loader.hide();
        navIntentArmed = false;
      }, 5000); // ← 5s に延長（Safari/回線遅延でも消えにくい）
    }
    function disarmIntent() {
      navIntentArmed = false;
      if (pendingHideTimer) { clearTimeout(pendingHideTimer); pendingHideTimer = null; }
    }

    function maybeInstantShowForAnchor(a, e) {
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
      if (a.getAttribute('target') === '_blank') return false;
      if (a.hasAttribute('download')) return false;
      if (a.dataset.noLoader === 'true') return false;
      if (isModifiedClick(e)) return false;
      if (window.__loader) window.__loader.show();
      armIntent();
      return true;
    }
    function maybeInstantShowForForm(form) {
      if (form.getAttribute('target') === '_blank') return false;
      if (form.dataset.noLoader === 'true') return false;
      if (window.__loader) window.__loader.show();
      armIntent();
      return true;
    }

    // show on pointerdown/touchstart (earliest)
    const downHandler = (e) => {
      const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if (a && maybeInstantShowForAnchor(a, e)) return;

      const submitBtn = e.target && e.target.closest ? e.target.closest('button[type="submit"], input[type="submit"]') : null;
      if (submitBtn) {
        const form = submitBtn.form || submitBtn.closest('form');
        if (form) { maybeInstantShowForForm(form); return; }
      }
      const form = e.target && e.target.closest ? e.target.closest('form') : null;
      if (form) { maybeInstantShowForForm(form); }
    };
    document.addEventListener('pointerdown', downHandler, { capture: true, passive: true });
    document.addEventListener('touchstart', downHandler, { capture: true, passive: true });

    // Enter key
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const active = document.activeElement;
      if (!active) return;
      if (active.tagName === 'A' && active.hasAttribute('href')) {
        maybeInstantShowForAnchor(active, e);
      } else if (active.tagName === 'BUTTON' || (active.tagName === 'INPUT' && active.type === 'submit')) {
        const form = active.form || active.closest('form');
        if (form) maybeInstantShowForForm(form);
      }
    }, { capture: true });

    // perform navigation ourselves on click (keeps loader up)
    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      if (a.getAttribute('target') === '_blank') return;
      if (a.hasAttribute('download')) return;
      if (a.dataset.noLoader === 'true') return;
      if (isModifiedClick(e)) return;

      e.preventDefault();
      if (window.__loader) window.__loader.show(() => (window.location.href = href));
      else window.location.href = href;
      // 遷移実行したのでフェイルセーフ解除
      disarmIntent();
    }, { capture: true });

    // form submit
    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.getAttribute('target') === '_blank') return;
      if (form.dataset.noLoader === 'true') return;
      if (window.__loader) window.__loader.show();
      disarmIntent(); // 送信したので解除（beforeunload が来るはず）
    }, { capture: true });

    // actual page leave started
    window.addEventListener('beforeunload', () => disarmIntent(), { passive: true });
  }

  /* =========================================
     2) Bottom Tab + Submenu — unchanged
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
     Boot
  ========================================= */
  function start() {
    initLoader();
    initInstantHook();   // ← 修正点：押した瞬間に出し、そのまま維持
    initTabs();          // ← 変更なし
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();