// static/js/base.js
// Loader + Bottom Tab & Submenu (caret under tab). Robust init & event wiring.

(() => {
  // ============= Utilities =============
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ============= 1) Minimal Loader =============
  function initLoader() {
    if (document.getElementById('loading-overlay')) return;

    const style = document.createElement('style');
    style.id = 'loader-inline-style';
    style.textContent = `
      #loading-overlay{position:fixed;inset:0;z-index:9999;background:rgba(10,10,20,.95);
        display:none;opacity:0;transition:opacity .22s ease;display:flex;align-items:center;justify-content:center;flex-direction:column}
      #loading-overlay .loading-text{color:#0ff;font:700 22px/1.2 "Orbitron",system-ui;
        text-shadow:0 0 10px #0ff,0 0 20px #0ff}
      #loading-overlay .loading-bar{width:220px;height:6px;border-radius:4px;margin-top:12px;
        background:linear-gradient(90deg,#0ff,#f0f,#0ff);background-size:200% 100%;animation:loadslide 2s linear infinite}
      @keyframes loadslide{0%{background-position:0 0}100%{background-position:200% 0}}
    `;
    document.head.appendChild(style);

    const overlay = document.createElement('div');
    overlay.id = 'loading-overlay';
    overlay.innerHTML = `
      <div class="loading-text">Now Loading…</div>
      <div class="loading-bar"></div>
    `;
    document.body.appendChild(overlay);

    function show(cb){
      overlay.style.display = 'flex';
      requestAnimationFrame(() => {
        overlay.style.opacity = '1';
        if (cb) setTimeout(cb, 40);
      });
    }
    function hide(){
      overlay.style.opacity = '0';
      setTimeout(() => { overlay.style.display = 'none'; }, 200);
    }
    window.__loader = { show, hide };

    // startup
    show();
    window.addEventListener('load', hide, { passive: true });
    window.addEventListener('beforeunload', () => show(), { passive: true });
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });
  }

  // ============= 2) Tabs & Submenus =============
  function initTabs() {
    const tabBar = $('.bottom-tab');
    if (!tabBar) return; // ページに下タブがない

    // --- caret-row（ケアレット行）をタブ直下に必ず置く ---
    let caretRow = $('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      // 見た目が CSS ない環境でも最低限動くよう inline で補強
      caretRow.style.cssText = `
        position: fixed; left: 0; right: 0;
        bottom: calc(72px + env(safe-area-inset-bottom,0)); /* 下タブのすぐ上 */
        display: grid; grid-auto-flow: column; grid-auto-columns: 1fr;
        gap: 0; padding: 0 6px 4px;
        z-index: 1001; pointer-events: none; /* ボタンだけ反応 */
      `;
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

    // --- tab-actionbar（サブメニューボタンバー） ---
    let actionbar = $('.tab-actionbar');
    if (!actionbar) {
      actionbar = document.createElement('div');
      actionbar.className = 'tab-actionbar';
      actionbar.id = 'tab-actionbar';
      actionbar.setAttribute('role', 'menu');
      actionbar.style.cssText = `
        position: fixed; left: 8px; right: 8px;
        bottom: calc(96px + env(safe-area-inset-bottom,0));
        display: none; opacity: 0; transform: translateY(8px);
        flex-wrap: wrap; justify-content: center; align-items: center; gap: 8px; padding: 10px;
        border-radius: 14px; z-index: 1200; pointer-events: auto;
        background: rgba(10,12,24,.9);
        border: 1px solid rgba(0,255,255,.16);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        transition: opacity .18s ease, transform .18s ease;
      `;
      document.body.appendChild(actionbar);
    }

    // 状態管理
    let openKey = null;
    const map = new Map(); // key -> { tab, link, submenu, caretBtn }

    function rebuild() {
      caretRow.innerHTML = '';
      map.clear();

      const tabs = $$('.tab-item', tabBar);
      if (!tabs.length) return;

      caretRow.style.gridTemplateColumns = `repeat(${tabs.length}, 1fr)`;

      let seq = 0;
      tabs.forEach((tab) => {
        // 一意キー
        let key = tab.dataset.tabkey;
        if (!key) {
          key = `t${Date.now().toString(36)}_${(seq++).toString(36)}`;
          tab.dataset.tabkey = key;
        }

        // 飾りケアレットを排除
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(n => n.remove());

        const link = $('.tab-link', tab);
        const submenu = $('.sub-menu', tab);

        // ケアレット列：常に列を作る（高さ揃え & 横一列）
        const cell = document.createElement('div');
        cell.className = 'caret-cell';
        cell.style.cssText = `
          display:flex; align-items:center; justify-content:center; pointer-events:auto;
        `;

        let caretBtn = null;
        if (submenu) {
          caretBtn = document.createElement('button');
          caretBtn.type = 'button';
          caretBtn.className = 'caret-btn';
          caretBtn.textContent = '▾';
          caretBtn.setAttribute('aria-expanded', 'false');
          caretBtn.setAttribute('aria-controls', 'tab-actionbar');
          caretBtn.dataset.tabkey = key;
          // 最低限の見た目（CSS無くても押せる）
          caretBtn.style.cssText = `
            min-width: 28px; height: 22px; line-height: 22px;
            border-radius: 10px; border: 1px solid rgba(0,255,255,.3);
            background: rgba(0,20,30,.6); color: #bfe8ff;
            font-size: 12px; font-weight: 700; letter-spacing:.02em;
          `;
          cell.appendChild(caretBtn);
        } else {
          const ph = document.createElement('div');
          ph.className = 'caret-placeholder';
          ph.style.cssText = `height:22px;`;
          cell.appendChild(ph);
        }
        caretRow.appendChild(cell);

        map.set(key, { tab, link, submenu, caretBtn });
      });

      // --- イベント付与 ---

      // ケアレット：開閉
      map.forEach(({ caretBtn }, key) => {
        if (!caretBtn) return;
        caretBtn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (openKey === key) hideBar();
          else showBar(key);
        };
      });

      // タブ本体：サブメニューが無ければ遷移／あれば“閉じるのみ”
      map.forEach(({ link, submenu }, key) => {
        if (!link) return;
        link.onclick = (e) => {
          const href = link.getAttribute('href');
          const target = link.getAttribute('target') || '';
          // サブメニューあり → タブ本体は開閉しない。押したら閉じるだけ。
          if (submenu) {
            e.preventDefault();
            if (openKey === key) hideBar();
            return;
          }
          // サブメニューなし → ページ遷移
          if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
          e.preventDefault();
          if (window.__loader && typeof window.__loader.show === 'function') {
            window.__loader.show(() => (window.location.href = href));
          } else {
            window.location.href = href;
          }
        };
      });

      // 既に開いているキーが消えたら閉じる
      if (openKey && !map.has(openKey)) hideBar();
    }

    function showBar(key) {
      const rec = map.get(key);
      if (!rec || !rec.submenu) return;

      // 中身を再構成
      actionbar.innerHTML = '';
      const links = $$('a', rec.submenu);
      if (!links.length) {
        const none = document.createElement('span');
        none.className = 'ab-btn';
        none.textContent = 'メニューなし';
        none.style.cssText = `
          appearance:none;border:1px solid rgba(255,255,255,.18);
          background:linear-gradient(135deg,rgba(0,255,255,.15),rgba(255,0,255,.12));
          color:#eaf8ff;font-size:.9rem;font-weight:800;border-radius:999px;
          padding:10px 14px;white-space:nowrap;
        `;
        actionbar.appendChild(none);
      } else {
        links.forEach(a => {
          const href = a.getAttribute('href') || '#';
          const label = (a.textContent || '').trim();
          const target = a.getAttribute('target') || '';
          const btn = document.createElement('a');
          btn.className = 'ab-btn';
          btn.textContent = label;
          btn.href = href;
          btn.style.cssText = `
            appearance:none;border:1px solid rgba(255,255,255,.18);
            background:linear-gradient(135deg,rgba(0,255,255,.15),rgba(255,0,255,.12));
            color:#eaf8ff;font-size:.9rem;font-weight:800;border-radius:999px;
            padding:10px 14px;white-space:nowrap; text-decoration:none;
          `;
          btn.onclick = (e) => {
            if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
            e.preventDefault();
            if (window.__loader && typeof window.__loader.show === 'function') {
              window.__loader.show(() => (window.location.href = href));
            } else {
              window.location.href = href;
            }
          };
          actionbar.appendChild(btn);
        });
      }

      // ケアレット状態
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      if (rec.caretBtn) rec.caretBtn.setAttribute('aria-expanded', 'true');

      // 表示
      actionbar.style.display = 'flex';
      requestAnimationFrame(() => {
        actionbar.style.opacity = '1';
        actionbar.style.transform = 'translateY(0)';
      });
      openKey = key;
    }

    function hideBar() {
      actionbar.style.opacity = '0';
      actionbar.style.transform = 'translateY(8px)';
      setTimeout(() => {
        if (actionbar.style.opacity === '0') actionbar.style.display = 'none';
      }, 180);
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      openKey = null;
    }

    // 外側クリック / ESC / リサイズ
    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar  = !!e.target.closest('.tab-actionbar');
      const inRow  = !!e.target.closest('.caret-row');
      const inTabs = !!e.target.closest('.bottom-tab');
      if (!inBar && !inRow && !inTabs) hideBar();
    }, { passive: true });

    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    // 動的変化に追従
    const mo = new MutationObserver(() => rebuild());
    mo.observe(tabBar, { childList: true, subtree: true });

    // 初期構築
    rebuild();
  }

  // ============= Kick =============
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initLoader();
      initTabs();
    });
  } else {
    initLoader();
    initTabs();
  }
})();