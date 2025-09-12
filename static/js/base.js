document.addEventListener("DOMContentLoaded", function () {
  /* =========================
     ローディング（先にCSS注入）
  ========================= */
  const style = document.createElement("style");
  style.innerHTML = `
    #loading-overlay{
      position: fixed; inset: 0;
      background: rgba(10,10,20,0.95);
      display: none; flex-direction: column; justify-content: center; align-items: center;
      z-index: 9999; opacity: 0; transition: opacity .25s ease; contain: paint;
    }
    #loading-overlay .loading-text{
      font-family: "Orbitron", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans";
      font-size: 26px; font-weight: 700; color: #00eaff; margin-bottom: 20px;
      text-shadow: 0 0 8px #0ff, 0 0 16px #0ff, 0 0 32px #0ff, 0 0 48px #f0f;
      animation: loader-bounce 1.4s ease-in-out infinite, loader-flicker 1.8s ease-in-out infinite;
      will-change: transform, text-shadow; position: relative;
    }
    #loading-overlay .loading-text::before{
      content: attr(data-text); position: absolute; inset: 0; color: transparent; pointer-events: none; z-index: -1;
      text-shadow: 0 0 10px #0ff, 0 0 20px #0ff, 0 0 40px #0ff, 0 0 80px #f0f; filter: blur(.4px);
    }
    @keyframes loader-bounce{ 0%,20%,50%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-12px)} 60%{transform:translateY(-6px)} }
    @keyframes loader-flicker{
      0%,100%{ text-shadow:0 0 8px #0ff,0 0 16px #0ff,0 0 32px #0ff,0 0 48px #f0f; }
      50%{    text-shadow:0 0 12px #0ff,0 0 24px #0ff,0 0 48px #0ff,0 0 72px #f0f; }
    }
    #loading-overlay .loading-bar-container{
      width: 80%; max-width: 400px; height: 8px; background: rgba(0,255,255,0.2); border-radius: 4px;
      overflow: hidden; box-shadow: inset 0 0 12px #0ff; position: relative;
    }
    #loading-overlay .loading-bar{
      height: 100%; width: 100%; border-radius: 4px;
      background: linear-gradient(90deg, #0ff, #ff00ff, #0ff);
      box-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #f0f;
      animation: loader-slide 2.5s linear infinite, loader-pulse 1.6s ease-in-out infinite;
      will-change: transform, box-shadow; transform: translateX(-100%);
    }
    @keyframes loader-slide{ 0%{transform:translateX(-100%)} 100%{transform:translateX(100%)} }
    @keyframes loader-pulse{ 0%,100%{box-shadow:0 0 12px #0ff,0 0 24px #0ff,0 0 36px #f0f} 50%{box-shadow:0 0 18px #0ff,0 0 36px #0ff,0 0 60px #f0f} }
  `;
  document.head.appendChild(style);

  const loadingOverlay = document.createElement("div");
  loadingOverlay.id = "loading-overlay";
  loadingOverlay.innerHTML = `
    <div class="loading-text" data-text="Now Loading...">Now Loading...</div>
    <div class="loading-bar-container"><div class="loading-bar"></div></div>
  `;
  document.body.appendChild(loadingOverlay);

  function showLoading(cb) {
    loadingOverlay.style.display = 'flex';
    requestAnimationFrame(() => {
      loadingOverlay.style.opacity = '1';
      if (typeof cb === 'function') setTimeout(cb, 40);
    });
  }
  function hideLoading() {
    loadingOverlay.style.opacity = '0';
    setTimeout(() => { loadingOverlay.style.display = 'none'; }, 220);
  }

  showLoading();
  window.addEventListener("load", hideLoading);
  window.addEventListener("beforeunload", () => showLoading());
  window.addEventListener("pageshow", (e) => { if (e.persisted) hideLoading(); });

  /* =========================
     下タブ & サブメニュー
     - モバイル: ボトムシート
     - デスクトップ: ポップオーバー
  ========================= */
  const tabBar = document.querySelector('.bottom-tab');
  const tabItems = document.querySelectorAll('.bottom-tab .tab-item');
  if (tabBar && tabItems.length) {
    // バックドロップ（共有）
    const backdrop = document.createElement('div');
    backdrop.className = 'tab-backdrop';
    document.body.appendChild(backdrop);

    // 共有ボトムシート（モバイル）
    const sheet = document.createElement('div');
    sheet.className = 'bottom-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.innerHTML = `<ul class="sub-menu" role="menu"></ul>`;
    const sheetList = sheet.querySelector('.sub-menu');
    document.body.appendChild(sheet);

    // 共有ポップオーバー（デスクトップ）
    const pop = document.createElement('div');
    pop.className = 'popover-menu';
    pop.setAttribute('role', 'menu');
    pop.innerHTML = `<ul class="sub-menu"></ul>`;
    const popList = pop.querySelector('.sub-menu');
    document.body.appendChild(pop);

    let openFor = null;   // 開いている tab-item
    let lastFocus = null; // 戻す先

    function isDesktop() {
      return window.matchMedia('(min-width: 768px)').matches;
    }

    function closeMenus() {
      document.querySelectorAll('.bottom-tab .tab-item.open').forEach(t => t.classList.remove('open'));
      backdrop.classList.remove('show');
      sheet.classList.remove('show');
      pop.classList.remove('show');
      sheetList.innerHTML = '';
      popList.innerHTML = '';
      openFor = null;
      if (lastFocus) {
        try { lastFocus.focus({ preventScroll: true }); } catch {}
        lastFocus = null;
      }
      document.removeEventListener('keydown', onKeydown);
    }

    function onKeydown(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeMenus();
      }
    }

    function buildList(fromMenu, toList) {
      toList.innerHTML = '';
      fromMenu.querySelectorAll('a').forEach(a => {
        const li = document.createElement('li');
        const link = document.createElement('a');
        link.href = a.getAttribute('href') || '#';
        link.textContent = a.textContent || '';
        link.addEventListener('click', ev => {
          const href = link.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            ev.preventDefault();
            showLoading(() => window.location.href = href);
          }
        });
        li.appendChild(link);
        toList.appendChild(li);
      });
    }

    function openMenuFor(tabItem) {
      const submenu = tabItem.querySelector('.sub-menu');
      if (!submenu) return; // 念のため
      lastFocus = tabItem.querySelector('.tab-link') || tabItem;
      document.addEventListener('keydown', onKeydown);

      // 既存オープンなら閉じる
      if (openFor && openFor !== tabItem) {
        closeMenus();
      }

      tabItem.classList.add('open');
      backdrop.classList.add('show');
      openFor = tabItem;

      if (isDesktop()) {
        // ポップオーバー
        buildList(submenu, popList);
        const rect = tabItem.getBoundingClientRect();
        pop.style.left = Math.min(
          Math.max(8, rect.left + rect.width / 2 - 110),
          window.innerWidth - 220
        ) + 'px';
        pop.style.top = (rect.top - 10) + 'px';
        pop.classList.add('show');
        // 初項目にフォーカス
        const first = pop.querySelector('a');
        if (first) first.focus({ preventScroll: true });
      } else {
        // ボトムシート
        buildList(submenu, sheetList);
        sheet.classList.add('show');
        const first = sheet.querySelector('a');
        if (first) first.focus({ preventScroll: true });
      }
    }

    // クリック系
    tabItems.forEach(tab => {
      const tabLink = tab.querySelector('.tab-link');
      const submenu = tab.querySelector('.sub-menu');

      // サブメニューが存在するタブに印を付ける
      if (submenu) tab.classList.add('has-sub');

      // タブ押下
      tab.addEventListener('click', e => {
        // サブメニューリンク自体は個別で捕捉するのでここでは無視
        if (e.target.closest('.sub-menu a')) return;

        if (submenu) {
          // メニューを開閉
          if (tab.classList.contains('open')) {
            closeMenus();
          } else {
            openMenuFor(tab);
          }
        } else if (tabLink) {
          // 直接遷移
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            showLoading(() => window.location.href = href);
          }
        }
      }, { passive: false });

      // 長押しでメニュー（スマホ向け）
      let t0 = 0, holdTimer = null;
      tab.addEventListener('touchstart', () => {
        t0 = Date.now();
        if (submenu) {
          holdTimer = setTimeout(() => {
            if (!tab.classList.contains('open')) openMenuFor(tab);
          }, 450);
        }
      }, { passive: true });
      tab.addEventListener('touchend', e => {
        clearTimeout(holdTimer);
        const dt = Date.now() - t0;
        // 短押しは click に委ねる
        if (dt < 450) return;
        e.preventDefault();
      }, { passive: false });

      // サブメニュー内クリック（既存DOM内）はローディングして遷移
      if (submenu) {
        submenu.querySelectorAll('a').forEach(a => {
          a.addEventListener('click', ev => {
            const href = a.getAttribute('href');
            if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
              ev.preventDefault();
              showLoading(() => window.location.href = href);
            }
          });
        });
      }
    });

    // バックドロップ/画面外クリックで閉じる
    backdrop.addEventListener('click', closeMenus);
    document.addEventListener('click', e => {
      if (!e.target.closest('.bottom-tab') &&
          !e.target.closest('.bottom-sheet') &&
          !e.target.closest('.popover-menu')) {
        closeMenus();
      }
    });

    window.addEventListener('resize', () => {
      // 画面切替時の取りこぼしを防ぐ
      closeMenus();
    });
  }

  /* =========================
     現在ページ名自動セット
  ========================= */
  const currentURL = location.pathname;
  const currentPageNameEl = document.getElementById("current-page-name");
  if (currentPageNameEl) {
    const tabLinks = document.querySelectorAll(".tab-item .tab-link");
    let found = false;
    tabLinks.forEach(tabLink => {
      const href = tabLink.getAttribute("href");
      const nameSpan = tabLink.querySelector("span");
      if (href && nameSpan && currentURL.startsWith(href)) {
        currentPageNameEl.textContent = nameSpan.textContent;
        found = true;
      }
    });
    if (!found) currentPageNameEl.textContent = currentURL.replace(/^\/|\/$/g, "") || "ホーム";
  }
});