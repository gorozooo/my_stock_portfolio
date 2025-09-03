// base.js（確認モーダル＋文字＋ネオン進捗バー 完全版・修正版）
document.addEventListener("DOMContentLoaded", function() {

  /* ========================================
     ===== 下タブ＆サブメニュー操作 ===== */
  const tabs = document.querySelectorAll('.tab-item');

  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    if (!subMenu) return;

    let touchTimer;

    // 右クリックでサブメニュー表示（PC用）
    tab.addEventListener('contextmenu', e => {
      e.preventDefault();
      closeAllSubMenus();
      openSubMenu(subMenu, tab);
    });

    // 長押しでサブメニュー表示（タッチ用）
    tab.addEventListener('touchstart', e => {
      touchTimer = setTimeout(() => {
        closeAllSubMenus();
        openSubMenu(subMenu, tab);
      }, 500);
    });
    tab.addEventListener('touchend', () => clearTimeout(touchTimer));
    tab.addEventListener('touchcancel', () => clearTimeout(touchTimer));
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.tab-item')) {
      closeAllSubMenus();
    }
  });

  function openSubMenu(subMenu, tab) {
    const rect = tab.getBoundingClientRect();
    subMenu.style.left = rect.left + "px";
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    subMenu.classList.add('show');
  }

  function closeAllSubMenus() {
    document.querySelectorAll('.sub-menu').forEach(sm => sm.classList.remove('show'));
  }

  /* ========================================
     ===== 背景アニメーション ===== */
  const canvas = document.getElementById('bgCanvas');
  if (canvas && canvas.getContext) {
    const ctx = canvas.getContext('2d');
    let width = window.innerWidth;
    let height = window.innerHeight;
    canvas.width = width;
    canvas.height = height;

    const particles = [];
    const PARTICLE_COUNT = 80;

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        size: Math.random() * 3 + 1,
        hue: Math.random() * 360
      });
    }

    function animateParticles() {
      ctx.clearRect(0, 0, width, height);
      particles.forEach(p => {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `hsl(${p.hue}, 100%, 50%)`;
        ctx.shadowColor = `hsl(${p.hue}, 100%, 60%)`;
        ctx.shadowBlur = 8;
        ctx.fill();
      });
      requestAnimationFrame(animateParticles);
    }
    animateParticles();

    window.addEventListener('resize', () => {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width;
      canvas.height = height;
    });
  }

  /* ========================================
     ===== 共通確認モーダル処理 ===== */
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

    btnCancel.addEventListener("click", () => {
      modal.style.display = "none";
      okCallback = null;
    });

    btnOk.addEventListener("click", () => {
      modal.style.display = "none";
      if (typeof okCallback === "function") okCallback();
      okCallback = null;
    });

    modal.addEventListener("click", e => {
      if (e.target === modal) {
        modal.style.display = "none";
        okCallback = null;
      }
    });
  }

  /* ========================================
     ===== ローディング画面（文字＋ネオン進捗バー） ===== */
  const loadingOverlay = document.createElement('div');
  Object.assign(loadingOverlay.style, {
    position: 'fixed', top: '0', left: '0',
    width: '100%', height: '100%',
    background: 'rgba(0,0,20,0.85)',
    display: 'flex', flexDirection: 'column',
    justifyContent: 'center', alignItems: 'center',
    zIndex: '9999', opacity: '0',
    transition: 'opacity 0.2s ease'
  });

  const loadingText = document.createElement('div');
  loadingText.textContent = 'Now Loading';
  Object.assign(loadingText.style, {
    color: '#0ff', fontFamily: '"Orbitron", sans-serif',
    fontSize: '1.5rem', marginBottom: '12px',
    textShadow: '0 0 8px #0ff, 0 0 16px #0ff',
    animation: 'bounceText 1s infinite'
  });
  loadingOverlay.appendChild(loadingText);

  const barContainer = document.createElement('div');
  Object.assign(barContainer.style, {
    width: '80%', maxWidth: '400px',
    height: '6px',
    background: 'rgba(0,255,255,0.1)',
    borderRadius: '3px', overflow: 'hidden',
    boxShadow: '0 0 12px #0ff inset',
    position: 'relative'
  });

  const loadingBar = document.createElement('div');
  Object.assign(loadingBar.style, {
    width: '0%', height: '100%',
    background: 'linear-gradient(90deg, #0ff, #0ff, #ff00ff, #0ff)',
    borderRadius: '3px',
    boxShadow: '0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #ff00ff',
    animation: 'neonPulse 1.5s infinite alternate',
    transition: 'width 0.2s linear',
    position: 'absolute',
    left: '0',
    top: '0'
  });
  barContainer.appendChild(loadingBar);
  loadingOverlay.appendChild(barContainer);

  document.body.appendChild(loadingOverlay);

  let loadingInterval;
  let progress = 0;

  function showLoading(callback) {
    loadingOverlay.style.display = 'flex';
    requestAnimationFrame(() => loadingOverlay.style.opacity = '1');

    progress = 0;
    loadingBar.style.width = '0%';

    clearInterval(loadingInterval);
    loadingInterval = setInterval(() => {
      progress += Math.random() * 6; // 少し早めに増加
      if (progress >= 95) progress = 95;
      loadingBar.style.width = progress + '%';
    }, 120);

    if (callback) setTimeout(callback, 50);
  }

  function hideLoading() {
    clearInterval(loadingInterval);
    loadingBar.style.width = '100%';
    setTimeout(() => {
      loadingOverlay.style.opacity = '0';
      setTimeout(() => {
        loadingOverlay.style.display = 'none';
        loadingBar.style.width = '0%';
      }, 300);
    }, 200);
  }

  // ページ完全ロードで終了
  window.addEventListener("load", hideLoading);
  window.addEventListener("pageshow", hideLoading);

  // すべてのリンクに適用
  document.querySelectorAll("a[href]").forEach(link => {
    link.addEventListener("click", e => {
      const href = link.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      e.preventDefault();
      showLoading(() => window.location.assign(href));
    });
  });

  // フォーム送信時
  document.querySelectorAll("form").forEach(form => {
    form.addEventListener("submit", () => showLoading());
  });

  // ページ離脱時にも表示
  window.addEventListener("beforeunload", () => showLoading());

  /* ===========================================
     ===== 現在ページ名を下タブから自動取得 =====
  ============================================ */
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
    if (!found) {
      const trimmed = currentURL.replace(/^\/|\/$/g, "");
      currentPageNameEl.textContent = trimmed || "ホーム";
    }
  }

  /* ===========================================
     ===== ローディングテキスト＋バーアニメーション =====
  ============================================ */
  const style = document.createElement('style');
  style.innerHTML = `
  @keyframes bounceText {
    0%, 20%, 50%, 80%, 100% { transform: translateY(0); }
    40% { transform: translateY(-16px); }
    60% { transform: translateY(-8px); }
  }
  @keyframes neonPulse {
    0% { box-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #ff00ff; }
    50% { box-shadow: 0 0 24px #0ff, 0 0 48px #0ff, 0 0 72px #ff00ff; }
    100% { box-shadow: 0 0 12px #0ff, 0 0 24px #0ff, 0 0 36px #ff00ff; }
  }`;
  document.head.appendChild(style);

});