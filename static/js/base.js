// static/js/base.js
// Loader + Bottom Tab & Submenu (all-in-one)
// - Loader: restores your previous behavior (always show on start, hide on window.load,
//           show on beforeunload, skip on bfcache restore).
// - Tabs  : caret-row under the bottom tab, actionbar buttons rendered from each .sub-menu.
// NOTE: このファイルだけで完結。下タブ/サブメニュー用の別JSは不要です。

(function () {
  /* =========================================
     1) Loader — “前の挙動”を完全再現
  ========================================= */
  function initLoader() {
    // まずテンプレ既存 (#loading-screen) を優先利用（派手版）
    let host = document.querySelector('#loading-screen');
    let mode = 'screen'; // 'screen' = 既存テンプレ, 'overlay' = 簡易オーバーレイ自動生成

    if (!host) {
      // 既存がなければ簡易オーバーレイを自動生成
      mode = 'overlay';

      // 見た目の最小CSS（外部CSSなしでも動作）
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
        // 既存スクリーン：非表示なら表示にして不透明に
        const cs = getComputedStyle(host);
        if (cs.display === 'none') host.style.display = 'flex';
        host.style.opacity = '1';
      } else {
        // 簡易オーバーレイ
        host.style.display = 'flex';
        requestAnimationFrame(() => { host.style.opacity = '1'; });
      }
      if (typeof cb === 'function') setTimeout(cb, 40); // 体感即時
    }

    function hide() {
      host.style.opacity = '0';
      const delay = (mode === 'screen') ? 220 : 200;
      setTimeout(() => {
        if (getComputedStyle(host).opacity === '0') host.style.display = 'none';
      }, delay);
    }

    // 公開
    window.__loader = { show, hide };

    // === 前の挙動 ===
    // A) 初回は必ず表示 → window.load で閉じる
    if (getComputedStyle(host).display === 'none') {
      show();
    } else {
      host.style.opacity = '1';
    }
    // B) ロード完了で閉じる
    window.addEventListener('load', hide, { passive: true });
    // C) ページ離脱時は常に表示（Safari含む）
    window.addEventListener('beforeunload', () => show(), { passive: true });
    // D) bfcache 復帰はローダー不要
    window.addEventListener('pageshow', (e) => { if (e.persisted) hide(); }, { passive: true });

    // E) ナビゲーション補助（任意で使用可）
    window.__goto = function (href) {
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      show(() => { window.location.href = href; });
    };
  }

  /* =========================================
     2) Bottom Tab + Submenu（data-tabkeyで厳密紐付け）
     - ケアレットは下タブの「直下」に横並びで表示
     - サブメニューは共通ボタンバー（.tab-actionbar）にレンダリング
     - ここは以前あなたが使っていた仕様をそのまま統合
  ========================================= */
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return; // 下タブがないページでは何もしない

    // ケアレット行（タブの直後に1つだけ）
    let caretRow = document.querySelector('.caret-row');
    if (!caretRow) {
      caretRow = document.createElement('div');
      caretRow.className = 'caret-row';
      // デザインはCSS側に任せる（ここでは生成のみ）
      tabBar.insertAdjacentElement('afterend', caretRow);
    }

    // アクションバー（共用）
    let actionbar = document.querySelector('.tab-actionbar');
    if (!actionbar) {
      actionbar = document.createElement('div');
      actionbar.className = 'tab-actionbar';
      document.body.appendChild(actionbar);
    }

    let openKey = null;
    const map = new Map(); // key -> { tab, link, submenu, caretBtn }

    function go(href) {
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
      if (window.__loader && typeof window.__loader.show === 'function') {
        window.__loader.show(() => (window.location.href = href));
      } else {
        window.location.href = href;
      }
    }

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

        // 飾りケアレットを掃除（重複防止）
        tab.querySelectorAll('.tab-caret, .caret, .caret-icon, [data-caret], [data-role="caret"]').forEach(n => n.remove());

        const link = tab.querySelector('.tab-link');
        const submenu = tab.querySelector('.sub-menu');

        // ケアレット行：常にタブ数分のセルを作る
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

      // ケアレット → 開閉
      map.forEach(({ caretBtn }, key) => {
        if (!caretBtn) return;
        caretBtn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (openKey === key) hideBar();
          else showBar(key);
        };
      });

      // タブ本体 → 通常遷移（go()経由でローダー表示）
      map.forEach(({ link }) => {
        if (!link) return;
        link.onclick = (e) => {
          const href = link.getAttribute('href');
          const target = link.getAttribute('target') || '';
          if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
          e.preventDefault();
          go(href);
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
          btn.onclick = (e) => {
            if (!href || href.startsWith('#') || href.startsWith('javascript:') || target === '_blank') return;
            e.preventDefault();
            go(href);
          };
          actionbar.appendChild(btn);
        });
      }

      // ケアレットの aria 更新
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      if (rec.caretBtn) rec.caretBtn.setAttribute('aria-expanded', 'true');

      // 表示（位置はCSSで固定）
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

    // 外側クリック / Esc / リサイズで閉じる
    document.addEventListener('click', (e) => {
      if (!openKey) return;
      const inBar  = !!e.target.closest('.tab-actionbar');
      const inRow  = !!e.target.closest('.caret-row');
      const inTabs = !!e.target.closest('.bottom-tab');
      if (!inBar && !inRow && !inTabs) hideBar();
    }, { passive: true });
    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) hideBar(); }, { passive: true });
    window.addEventListener('resize', hideBar, { passive: true });

    // 下タブ内部の変化に追従
    const mo = new MutationObserver(() => rebuild());
    mo.observe(tabBar, { childList: true, subtree: true });

    // 初期構築
    rebuild();
  }

  /* =========================================
     起動
  ========================================= */
  function start() {
    initLoader();
    initTabs(); // 下タブ/サブメニューもこのファイルで動かす
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();