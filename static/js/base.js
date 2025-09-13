// static/js/base.js
// Loader + Bottom Tab/Submenu combined, with aggressive instant-show
// - pointerdown/touchstart で即表示（hidden/visibility/display/opacity を !important で上書き）
// - click は preventDefault -> loader を“出しっぱなし”で href へ
// - #loading-screen があればそれを使用 / 無ければ最上位オーバーレイを自動生成
// - 下タブ/サブメニューは“開閉のみ”維持（遷移はグローバルで拾う）

(function () {
  /* ================ helpers ================ */
  const isModClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
  const isValidHref = (href) => href && !href.startsWith('#') && !href.startsWith('javascript:');

  /* ================ Loader core ================ */
  function createFallbackOverlay() {
    const style = document.createElement('style');
    style.id = '__hardloader_css__';
    style.textContent = `
      #__hardloader__{
        position:fixed !important; inset:0 !important; z-index:2147483647 !important;
        background:rgba(10,10,20,.95) !important;
        display:none !important; opacity:0 !important; transition:opacity .18s ease !important;
        display:flex !important; align-items:center !important; justify-content:center !important; flex-direction:column !important;
        pointer-events:all !important;
      }
      #__hardloader__ .loading-text{
        color:#0ff !important; font:700 22px/1.2 "Orbitron",system-ui !important;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff !important;
      }
      #__hardloader__ .loading-bar{
        width:220px !important; height:6px !important; border-radius:4px !important; margin-top:12px !important;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff) !important; background-size:200% 100% !important;
        animation:__hardloader_slide 2s linear infinite !important;
      }
      @keyframes __hardloader_slide { 0%{background-position:0 0} 100%{background-position:200% 0} }
    `;
    document.head.appendChild(style);

    const host = document.createElement('div');
    host.id = '__hardloader__';
    host.innerHTML = `<div class="loading-text">Now Loading…</div><div class="loading-bar"></div>`;
    document.body ? document.body.appendChild(host) : document.addEventListener('DOMContentLoaded', () => document.body.appendChild(host), { once: true });
    return host;
  }

  function initLoader() {
    // 1) 既存の #loading-screen を優先利用
    let host = document.querySelector('#loading-screen');
    let usingFallback = false;

    if (!host) {
      host = createFallbackOverlay();
      usingFallback = true;
    }

    // 2) 最後に必ず勝つ show/hide（!important で上書き & hiddenクラス剥がし）
    function forceShow(cb) {
      try {
        host.classList && host.classList.remove('hidden');
      } catch {}
      host.style.setProperty('display', 'flex', 'important');
      host.style.setProperty('visibility', 'visible', 'important');
      // reflow 確保
      // eslint-disable-next-line no-unused-expressions
      host.offsetHeight;
      host.style.setProperty('opacity', '1', 'important');
      host.style.setProperty('z-index', '2147483647', 'important');
      document.documentElement.style.cursor = 'wait';
      document.body.style.cursor = 'wait';
      if (typeof cb === 'function') cb();
    }

    function forceHide() {
      host.style.setProperty('opacity', '0', 'important');
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') {
          host.style.setProperty('display', 'none', 'important');
          host.style.setProperty('visibility', 'hidden', 'important');
          document.documentElement.style.cursor = '';
          document.body.style.cursor = '';
        }
      }, 230);
    }

    // 3) 公開
    window.__loader = { show: forceShow, hide: forceHide };

    // 4) “前の挙動”に戻す：初期は必ず表示 → load で閉じる
    forceShow();
    window.addEventListener('load', forceHide, { passive: true });
    window.addEventListener('beforeunload', () => forceShow(), { passive: true });
    window.addEventListener('pageshow', (e) => { if (e.persisted) forceHide(); }, { passive: true });

    // 5) 既存 loader.js（PageLoader）がいても上書き勝ちするように同期
    if (window.PageLoader) {
      const origShow = window.PageLoader.show?.bind(window.PageLoader);
      const origHide = window.PageLoader.hide?.bind(window.PageLoader);
      window.PageLoader.show = () => { origShow?.(); forceShow(); };
      window.PageLoader.hide = () => { forceHide(); origHide?.(); };
    }

    return { host, usingFallback };
  }

  /* ================ Instant Hook ================ */
  function initInstantHook() {
    // pointerdown/touchstart で最速表示（描画を間に合わせる）
    const down = (e) => {
      const a = e.target.closest?.('a[href]');
      const btn = e.target.closest?.('button[type="submit"], input[type="submit"]');
      if (a) {
        const href = a.getAttribute('href');
        if (!isModClick(e) && isValidHref(href) && a.target !== '_blank' && !a.hasAttribute('download') && a.dataset.noLoader !== 'true') {
          window.__loader?.show();
        }
      } else if (btn) {
        const form = btn.form || btn.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          window.__loader?.show();
        }
      }
    };
    document.addEventListener('pointerdown', down, { capture: true, passive: true });
    document.addEventListener('touchstart', down, { capture: true, passive: true });

    // click は防いで自分でナビゲーション（表示を維持）
    document.addEventListener('click', (e) => {
      const a = e.target.closest?.('a[href]');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!isValidHref(href)) return;
      if (a.target === '_blank' || a.hasAttribute('download') || a.dataset.noLoader === 'true') return;
      if (isModClick(e)) return;

      e.preventDefault();
      window.__loader?.show(() => { window.location.href = href; });
    }, { capture: true });

    // form 送信
    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.target === '_blank' || form.dataset.noLoader === 'true') return;
      window.__loader?.show();
    }, { capture: true });

    // Enterキー（フォーカスが a / submit の場合）
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const el = document.activeElement;
      if (!el) return;
      if (el.tagName === 'A' && el.hasAttribute('href')) {
        const href = el.getAttribute('href');
        if (isValidHref(href)) window.__loader?.show();
      } else if (el.tagName === 'BUTTON' || (el.tagName === 'INPUT' && el.type === 'submit')) {
        const form = el.form || el.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') window.__loader?.show();
      }
    }, { capture: true });
  }

  /* ================ Bottom Tab / Submenu（開閉のみ） ================ */
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
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach((n) => n.remove());

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
          if (target) btn.target = target;
          btn.textContent = label;
          // 遷移はグローバル click フックに任せる
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

  /* ================ Boot ================ */
  function start() {
    initLoader();
    initInstantHook();  // 押下の瞬間にローダー出す（維持）
    initTabs();         // 開閉のみ（遷移はグローバルで）
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();