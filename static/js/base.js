document.addEventListener("DOMContentLoaded", function () {

  /* =====================================
     ローディング用スタイル（先に注入）
  ===================================== */
  const style = document.createElement("style");
  style.innerHTML = `
    #loading-overlay{
      position: fixed; inset: 0; background: rgba(10,10,20,0.95);
      display: none; flex-direction: column; justify-content: center; align-items: center;
      z-index: 9999; opacity: 0; transition: opacity .25s ease; contain: paint;
    }
    #loading-overlay .loading-text{
      font-family: "Orbitron", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans";
      font-size: 26px; font-weight: 700; color: #00eaff; margin-bottom: 20px;
      text-shadow: 0 0 8px #0ff, 0 0 16px #0ff, 0 0 32px #0ff, 0 0 48px #f0f;
      animation: loader-bounce 1.4s ease-in-out infinite, loader-flicker 1.8s ease-in-out infinite;
      will-change: transform, text-shadow; transform: translateZ(0); position: relative;
    }
    #loading-overlay .loading-text::before{
      content: attr(data-text); position: absolute; inset: 0; color: transparent; pointer-events: none; z-index: -1;
      text-shadow: 0 0 10px #0ff, 0 0 20px #0ff, 0 0 40px #0ff, 0 0 80px #f0f; filter: blur(.4px);
    }
    @keyframes loader-bounce{ 0%,20%,50%,80%,100%{ transform: translateZ(0) translateY(0) } 40%{ transform: translateZ(0) translateY(-12px) } 60%{ transform: translateZ(0) translateY(-6px) } }
    @keyframes loader-flicker{
      0%,100%{ text-shadow: 0 0 8px #0ff, 0 0 16px #0ff, 0 0 32px #0ff, 0 0 48px #f0f; }
      50%{    text-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 48px #0ff, 0 0 72px #f0f; }
    }
    #loading-overlay .loading-bar-container{
      width: 80%; max-width: 400px; height: 8px; background: rgba(0,255,255,0.2);
      border-radius: 4px; overflow: hidden; box-shadow: inset 0 0 12px #0ff; position: relative;
    }
    #loading-overlay .loading-bar{
      height: 100%; width: 100%; border-radius: 4px;
      background: linear-gradient(90deg, #0ff, #ff00ff, #0ff); box-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #f0f;
      animation: loader-slide 2.5s linear infinite, loader-pulse 1.6s ease-in-out infinite;
      will-change: transform, box-shadow; transform: translateZ(0) translateX(-100%);
    }
    @keyframes loader-slide{ 0%{ transform: translateZ(0) translateX(-100%) } 100%{ transform: translateZ(0) translateX(100%) } }
    @keyframes loader-pulse{
      0%,100%{ box-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #f0f; }
      50%{    box-shadow: 0 0 18px #0ff, 0 0 36px #0ff, 0 0 60px #f0f; }
    }
  `;
  document.head.appendChild(style);

  /* =====================================
     ローディングDOM（テンプレ文字列で挿入）
  ===================================== */
  const loadingOverlay = document.createElement("div");
  loadingOverlay.id = "loading-overlay";
  loadingOverlay.innerHTML = `
    <div class="loading-text" data-text="Now Loading...">Now Loading...</div>
    <div class="loading-bar-container">
      <div class="loading-bar"></div>
    </div>
  `;
  document.body.appendChild(loadingOverlay);

  function showLoading(cb) {
    loadingOverlay.style.display = 'flex';
    requestAnimationFrame(() => {
      loadingOverlay.style.opacity = '1';
      if (typeof cb === 'function') setTimeout(cb, 50);
    });
  }
  function hideLoading() {
    loadingOverlay.style.opacity = '0';
    setTimeout(() => { loadingOverlay.style.display = 'none'; }, 260);
  }

  showLoading();
  window.addEventListener("load", hideLoading);
  window.addEventListener("beforeunload", () => showLoading());
  window.addEventListener("pageshow", (e) => { if (e.persisted) hideLoading(); });

  /* =====================================
     下タブ＆サブメニュー（修正版）
  ===================================== */
  const tabs = document.querySelectorAll('.tab-item');

  // まず全サブメニューを初期化
  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    if (subMenu) {
      // show/close を CSS で制御するので display は触らない
      subMenu.classList.remove('show');
      // 固定配置に（重なり事故防止）
      subMenu.style.position = 'fixed';
      subMenu.style.opacity = '0';
      subMenu.style.transform = 'translateY(10px)';
      subMenu.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
      subMenu.style.zIndex = '10000';
    }
  });

  // サブメニューを開く（座標合わせ含む）
  function openSubMenu(subMenu, tab) {
    const rect = tab.getBoundingClientRect();
    // 一旦表示して幅を取る
    subMenu.style.visibility = 'hidden';
    subMenu.classList.add('show');
    // 左位置（中央寄せしつつはみ出し防止）
    const w = subMenu.getBoundingClientRect().width || 160;
    const left = Math.min(Math.max(8, rect.left + rect.width/2 - w/2), window.innerWidth - w - 8);
    subMenu.style.left = left + "px";
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    // フェードイン
    requestAnimationFrame(() => {
      subMenu.style.visibility = 'visible';
      subMenu.style.opacity = '1';
      subMenu.style.transform = 'translateY(0)';
    });
  }

  function closeAllSubMenus() {
    document.querySelectorAll('.sub-menu.show').forEach(sm => {
      sm.classList.remove('show');
      sm.style.opacity = '0';
      sm.style.transform = 'translateY(10px)';
      // 位置リセットは不要（次回再計算）
    });
  }

  // タブごとのイベント
  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    const tabLink = tab.querySelector('.tab-link');

    // サブメニュー内リンク：遷移前にローディング
    if (subMenu) {
      subMenu.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', e => {
          const href = a.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            e.stopPropagation();
            showLoading(() => window.location.href = href);
          }
        });
        a.addEventListener('touchend', e => {
          const href = a.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            e.stopPropagation();
            showLoading(() => window.location.href = href);
          }
        });
      });
    }

    // ★ ここが肝：サブメニューがあるタブは tabLink クリックで開閉し、遷移しない
    if (tabLink) {
      tabLink.addEventListener('click', e => {
        if (subMenu) {
          e.preventDefault(); // ← ナビゲーションさせない
          const isOpen = subMenu.classList.contains('show');
          closeAllSubMenus();
          if (!isOpen) openSubMenu(subMenu, tab);
        } else {
          // サブメニューが無いタブは通常遷移＋ローダー
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            closeAllSubMenus();
            showLoading(() => window.location.href = href);
          }
        }
      });

      // モバイル長押し：サブメニューをクイックオープン
      let touchStartTime = 0;
      tabLink.addEventListener('touchstart', () => { touchStartTime = Date.now(); }, {passive:true});
      tabLink.addEventListener('touchend', e => {
        if (!subMenu) return;
        const dur = Date.now() - touchStartTime;
        if (dur >= 500) {
          e.preventDefault();
          e.stopPropagation();
          const isOpen = subMenu.classList.contains('show');
          closeAllSubMenus();
          if (!isOpen) openSubMenu(subMenu, tab);
        }
      }, {passive:false});
    }

    // サブメニュー自体のクリックはバブリング停止
    if (subMenu) {
      subMenu.addEventListener('click', e => e.stopPropagation());
    }
  });

  // 外側クリック/タッチで閉じる
  ['click','touchstart'].forEach(ev => {
    document.addEventListener(ev, e => {
      if (!e.target.closest('.tab-item') && !e.target.closest('.sub-menu')) {
        closeAllSubMenus();
      }
    }, {passive:true});
  });
});