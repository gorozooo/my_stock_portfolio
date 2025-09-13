// static/js/base.js
// Loader + Bottom Tab/Submenu（標準遷移のまま）
// ローダーは「必ず閉じる」多重セーフ付き

(function () {
  /* =========================================
     0) helpers
  ========================================= */
  const isModClick = (e) => e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0;
  const isValidHref = (href) => href && !href.startsWith('#') && !href.startsWith('javascript:');

  /* =========================================
     1) Loader（#loading-screen 優先／無ければ簡易生成）
  ========================================= */
  function initLoader() {
    let host = document.querySelector('#loading-screen');
    let mode = 'screen'; // 'screen' = 既存テンプレ, 'overlay' = 自動生成

    if (!host) {
      mode = 'overlay';

      const style = document.createElement('style');
      style.textContent = `
        #__loading_overlay__{
          position:fixed; inset:0; z-index:9999;
          background:rgba(10,10,20,.95);
          display:flex; align-items:center; justify-content:center; flex-direction:column;
          opacity:1; transition:opacity .35s ease, visibility .35s ease;
          visibility:visible;
        }
        #__loading_overlay__.hidden{
          opacity:0; visibility:hidden; pointer-events:none;
        }
        #__loading_overlay__ .loading-text{
          color:#0ff; font:700 22px/1.2 "Orbitron",system-ui;
          text-shadow:0 0 10px #0ff,0 0 20px #0ff; margin-bottom:12px;
        }
        #__loading_overlay__ .loading-bar{
          width:220px; height:6px; border-radius:4px;
          background:linear-gradient(90deg,#0ff,#f0f,#0ff); background-size:200% 100%;
          animation:__slide 2s linear infinite;
        }
        @keyframes __slide { 0%{background-position:0 0} 100%{background-position:200% 0} }
      `;
      document.head.appendChild(style);

      host = document.createElement('div');
      host.id = '__loading_overlay__';
      host.innerHTML = `
        <div class="loading-text" aria-live="polite">Now Loading…</div>
        <div class="loading-bar" role="progressbar" aria-hidden="true"></div>
      `;
      document.body.appendChild(host);
    }

    // 既存CSS互換：hidden で非表示／それ以外は表示
    const show = () => {
      host.classList.remove('hidden');
      host.style.pointerEvents = 'auto'; // 表示中はクリックブロック
      document.documentElement.style.cursor = 'wait';
      document.body.style.cursor = 'wait';
    };
    const hide = () => {
      host.classList.add('hidden');
      host.style.pointerEvents = 'none';
      document.documentElement.style.cursor = '';
      document.body.style.cursor = '';
    };

    // グローバル API
    window.PageLoader = { show, hide };

    // ====== “前の挙動”＋多重セーフ ======
    // A) 初回は必ず表示
    show();

    // B) window.load で消す（once）
    window.addEventListener('load', () => {
      setTimeout(hide, 250);
    }, { passive: true, once: true });

    // C) もしこの時点で既に読み終わっていたら即消す（script遅延で load 済のケース）
    if (document.readyState === 'complete') {
      setTimeout(hide, 0);
    }

    // D) visibilitychange で復帰時に読み終わっていれば消す（iOS/Safari 対策）
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && document.readyState === 'complete') hide();
    }, { passive: true });

    // E) bfcache 復帰はローダー不要
    window.addEventListener('pageshow', (e) => {
      if (e.persisted) hide();
    }, { passive: true });

    // F) 離脱時は必ず表示（遷移開始を体感させる）
    window.addEventListener('beforeunload', () => { show(); }, { passive: true });

    // G) クリックの“押下瞬間”に早表示（遷移は標準に任せる。preventDefaultしない）
    const earlyShow = (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      const submitBtn = e.target.closest && e.target.closest('button[type="submit"], input[type="submit"]');
      if (a) {
        const href = a.getAttribute('href');
        if (!isModClick(e) && isValidHref(href) && a.target !== '_blank' && !a.hasAttribute('download') && a.dataset.noLoader !== 'true') {
          show();
        }
      } else if (submitBtn) {
        const form = submitBtn.form || submitBtn.closest('form');
        if (form && form.target !== '_blank' && form.dataset.noLoader !== 'true') {
          show();
        }
      }
    };
    document.addEventListener('pointerdown', earlyShow, { capture: true, passive: true });
    document.addEventListener('touchstart', earlyShow, { capture: true, passive: true });

    // H) 予防策：何があっても最大 8 秒で自動クローズ（無限ローディング防止）
    setTimeout(() => hide(), 8000);

    // I) JSエラー発生時も画面を見られるようにする
    window.addEventListener('error', () => hide());
    window.addEventListener('unhandledrejection', () => hide());
  }

  /* =========================================
     2) Bottom Tab + Submenu（UIのみ、遷移は標準）
  ========================================= */
  function initTabs() {
    const tabBar = document.querySelector('.bottom-tab');
    if (!tabBar) return;

    // ケアレット行（タブの直下に並べる）
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
    const map = new Map(); // key -> { tab, link, submenu, caretBtn }

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

        const link    = tab.querySelector('.tab-link');   // ← 触らない（標準遷移）
        const submenu = tab.querySelector('.sub-menu');

        // ケアレット列：常にセルを作る（高さを揃える）
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

      // ケアレットでのみ開閉（aのクリックは素通し）
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
          const btn = a.cloneNode(true);     // href/target そのまま維持
          btn.classList.add('ab-btn');       // 見た目だけ追加
          // クリックハンドラは付けない（標準遷移）
          actionbar.appendChild(btn);
        });
      }

      // ケアレット状態
      map.forEach(({ caretBtn }) => { if (caretBtn) caretBtn.setAttribute('aria-expanded', 'false'); });
      const recBtn = rec.caretBtn;
      if (recBtn) recBtn.setAttribute('aria-expanded', 'true');

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

    // 外側クリック/ESC/リサイズで閉じる（UIのみ）
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
    try { initLoader(); } catch (e) { console.error(e); }
    try { initTabs();   } catch (e) { console.error(e); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();