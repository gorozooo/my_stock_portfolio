document.addEventListener("DOMContentLoaded", function () {

  /* =====================================
     ローディング用スタイル（先に注入）
     ※ ここを最初に入れることで「ネオンが後から効く」現象を防ぐ
  ===================================== */
  const style = document.createElement("style");
  style.innerHTML = `
    /* ===== Overlay ===== */
    #loading-overlay{
      position: fixed;
      inset: 0;
      background: rgba(10,10,20,0.95);
      display: none;               /* 初期は非表示 */
      flex-direction: column;
      justify-content: center;
      align-items: center;
      z-index: 9999;
      opacity: 0;
      transition: opacity .25s ease;
      contain: paint;
    }

    /* ===== Text (最初からネオンON) ===== */
    #loading-overlay .loading-text{
      font-family: "Orbitron", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans";
      font-size: 26px;
      font-weight: 700;
      color: #00eaff;
      margin-bottom: 20px;

      /* 初期フレームから強い発光 */
      text-shadow:
        0 0 8px  #0ff,
        0 0 16px #0ff,
        0 0 32px #0ff,
        0 0 48px #f0f;

      /* “暗くしない”揺らめき & バウンド */
      animation:
        loader-bounce 1.4s ease-in-out infinite,
        loader-flicker 1.8s ease-in-out infinite;

      will-change: transform, text-shadow;
      transform: translateZ(0);
      position: relative;
    }

    /* 初期フレームでもネオンを保証する後光レイヤー */
    #loading-overlay .loading-text::before{
      content: attr(data-text);
      position: absolute;
      inset: 0;
      color: transparent;
      pointer-events: none;
      z-index: -1;
      text-shadow:
        0 0 10px #0ff,
        0 0 20px #0ff,
        0 0 40px #0ff,
        0 0 80px #f0f;
      filter: blur(.4px);
    }

    @keyframes loader-bounce{
      0%,20%,50%,80%,100%{ transform: translateZ(0) translateY(0); }
      40%{ transform: translateZ(0) translateY(-12px); }
      60%{ transform: translateZ(0) translateY(-6px); }
    }

    /* 明→より明（暗くしない） */
    @keyframes loader-flicker{
      0%,100%{
        text-shadow:
          0 0 8px  #0ff,
          0 0 16px #0ff,
          0 0 32px #0ff,
          0 0 48px #f0f;
      }
      50%{
        text-shadow:
          0 0 12px #0ff,
          0 0 24px #0ff,
          0 0 48px #0ff,
          0 0 72px #f0f;
      }
    }

    /* ===== Bar ===== */
    #loading-overlay .loading-bar-container{
      width: 80%;
      max-width: 400px;
      height: 8px;
      background: rgba(0,255,255,0.2);
      border-radius: 4px;
      overflow: hidden;
      box-shadow: inset 0 0 12px #0ff;
      position: relative;
    }

    #loading-overlay .loading-bar{
      height: 100%;
      width: 100%;
      border-radius: 4px;
      background: linear-gradient(90deg, #0ff, #ff00ff, #0ff);

      /* 初期から発光 */
      box-shadow:
        0 0 12px #0ff,
        0 0 24px #0ff,
        0 0 36px #f0f;

      /* 幅アニメではなく横スライドで常時流れる */
      animation:
        loader-slide 2.5s linear infinite,
        loader-pulse 1.6s ease-in-out infinite;
      will-change: transform, box-shadow;
      transform: translateZ(0) translateX(-100%);
    }

    @keyframes loader-slide{
      0%   { transform: translateZ(0) translateX(-100%); }
      100% { transform: translateZ(0) translateX(100%); }
    }

    /* 明るさだけ上下（暗くしない） */
    @keyframes loader-pulse{
      0%,100%{
        box-shadow:
          0 0 12px #0ff,
          0 0 24px #0ff,
          0 0 36px #f0f;
      }
      50%{
        box-shadow:
          0 0 18px #0ff,
          0 0 36px #0ff,
          0 0 60px #f0f;
      }
    }
  `;
  document.head.appendChild(style);

  /* =====================================
     ローディングDOM（スタイル注入後に生成）
  ===================================== */
  const loadingOverlay = document.createElement("div");
  loadingOverlay.id = "loading-overlay";
  loadingOverlay.innerHTML = 
    <div class="loading-text" data-text="Now Loading...">Now Loading...</div>
    <div class="loading-bar-container">
      <div class="loading-bar"></div>
    </div>
  ;
  document.body.appendChild(loadingOverlay);

  function showLoading(cb) {
    // 表示フラグ
    loadingOverlay.style.display = 'flex';
    // 次フレームでフェードイン
    requestAnimationFrame(() => {
      loadingOverlay.style.opacity = '1';
      if (typeof cb === 'function') {
        // 極小ディレイで遷移（体感即時）
        setTimeout(cb, 50);
      }
    });
  }

  function hideLoading() {
    loadingOverlay.style.opacity = '0';
    setTimeout(() => {
      loadingOverlay.style.display = 'none';
    }, 260);
  }

  // 初回：すぐ表示（CSSが先に入っているのでネオンは最初から効く）
  showLoading();

  // ロード完了で隠す（“最後だけ一瞬出る”感じを抑えるため短め）
  window.addEventListener("load", hideLoading);

  // Safariリロード/離脱時にも確実に表示
  window.addEventListener("beforeunload", function () {
    showLoading();
  });

  // bfcache 復帰（戻る/進む）はローダー不要
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) hideLoading();
  });

  /* =====================================
     下タブ＆サブメニュー
  ===================================== */
  const tabs = document.querySelectorAll('.tab-item');

  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    const tabLink = tab.querySelector('.tab-link');

    if (subMenu) {
      // サブメニュー初期スタイル
      subMenu.style.position = 'fixed';
      subMenu.style.opacity = '0';
      subMenu.style.transform = 'translateY(10px)';
      subMenu.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
      subMenu.style.zIndex = '10000';

      // サブメニューリンク：ページ遷移前にローディング
      subMenu.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', e => {
          e.stopPropagation();
          const href = a.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            showLoading(() => window.location.href = href);
          }
        });
        a.addEventListener('touchend', e => {
          e.stopPropagation();
          const href = a.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            showLoading(() => window.location.href = href);
          }
        });
      });

      // タブクリックでサブメニュー開閉
      tab.addEventListener('click', e => {
        if (e.target.closest('.sub-menu a')) return; // サブメニュー内リンクは無視
        const isOpen = subMenu.classList.contains('show');
        closeAllSubMenus();
        if (!isOpen) openSubMenu(subMenu, tab);
      });

      // サブメニュー内クリックはバブリング停止
      subMenu.addEventListener('click', e => e.stopPropagation());
    }

    // 下タブリンク：サブメニュー未表示ならページ遷移前にローディング
    if (tabLink) {
      tabLink.addEventListener('click', e => {
        if (subMenu && subMenu.classList.contains('show')) {
          e.preventDefault();
          closeAllSubMenus();
        } else {
          const href = tabLink.getAttribute('href');
          if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
            e.preventDefault();
            showLoading(() => window.location.href = href);
          }
        }
      });
    }

    // タブ長押し対応（スマホ向け）
    let touchStartTime = 0;
    tab.addEventListener('touchstart', () => { touchStartTime = Date.now(); });
    tab.addEventListener('touchend', e => {
      const touchDuration = Date.now() - touchStartTime;
      if (touchDuration < 500 && !e.target.closest('.sub-menu a')) tab.click();
    });
  });

  // 外部クリックでサブメニュー閉じる
  ['click', 'touchstart'].forEach(ev => {
    document.addEventListener(ev, e => {
      if (!e.target.closest('.tab-item')) closeAllSubMenus();
    });
  });

  function openSubMenu(subMenu, tab) {
    const rect = tab.getBoundingClientRect();
    const left = Math.min(rect.left, window.innerWidth - subMenu.offsetWidth - 10);
    subMenu.style.left = left + "px";
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    requestAnimationFrame(() => {
      subMenu.classList.add('show');
      subMenu.style.opacity = '1';
      subMenu.style.transform = 'translateY(0)';
    });
  }

  function closeAllSubMenus() {
    document.querySelectorAll('.sub-menu').forEach(sm => {
      sm.classList.remove('show');
      sm.style.opacity = '0';
      sm.style.transform = 'translateY(10px)';
    });
  }

  /* =====================================
     共通確認モーダル
  ===================================== */
  const modal = document.getElementById("confirmModal");
  if (modal) {
    const btnCancel = modal.querySelector(".btn-cancel");
    const btnOk = modal.querySelector(".btn-ok");
    let okCallback = null;

    window.openConfirmModal = (message, callback) => {
      modal.querySelector("p").textContent = message;
      okCallback = callback;
      modal.style.display = "block";
    };
    btnCancel.addEventListener("click", () => { modal.style.display = "none"; okCallback = null; });
    btnOk.addEventListener("click", () => { modal.style.display = "none"; if (typeof okCallback === "function") okCallback(); okCallback = null; });
    modal.addEventListener("click", e => { if (e.target === modal) { modal.style.display = "none"; okCallback = null; } });
  }

  /* =====================================
     現在ページ名自動取得
  ===================================== */
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