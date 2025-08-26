// base.js
document.addEventListener("DOMContentLoaded", function() {

  /* ========================================
     ===== 下タブ＆サブメニュー操作 ===== */
  const tabs = document.querySelectorAll('.tab-item');

  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');
    if (!subMenu) return; // サブメニューがないタブはスキップ

    let touchTimer;

    // PC右クリックでサブメニュー表示
    tab.addEventListener('contextmenu', e => {
      e.preventDefault();
      closeAllSubMenus();
      openSubMenu(subMenu, tab);
    });

    // スマホ長押しでサブメニュー表示
    tab.addEventListener('touchstart', e => {
      touchTimer = setTimeout(() => {
        closeAllSubMenus();
        openSubMenu(subMenu, tab);
      }, 500); // 0.5秒長押し
    });
    tab.addEventListener('touchend', e => clearTimeout(touchTimer));
    tab.addEventListener('touchcancel', e => clearTimeout(touchTimer));
  });

  // 背景クリックで全サブメニュー閉じる
  document.addEventListener('click', e => {
    if (!e.target.closest('.tab-item')) {
      closeAllSubMenus();
    }
  });

  function openSubMenu(subMenu, tab) {
    const rect = tab.getBoundingClientRect();
    subMenu.style.left = rect.left + "px"; // タブの左位置に合わせる
    subMenu.style.bottom = (window.innerHeight - rect.top + 10) + "px"; // タブ上に表示
    subMenu.classList.add('show');
  }

  function closeAllSubMenus() {
    document.querySelectorAll('.sub-menu').forEach(sm => sm.classList.remove('show'));
  }

  /* ========================================
     ===== 背景アニメーション（粒子＋ネオン光彩） ===== */
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
        // 位置更新
        p.x += p.vx;
        p.y += p.vy;

        // 画面端で反射
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;

        // 描画
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

    // リサイズ対応
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

    // モーダル表示関数をグローバルに公開
    window.openConfirmModal = (message, callback) => {
      modal.querySelector("p").textContent = message;
      okCallback = callback;
      modal.style.display = "block";
    };

    // キャンセルボタン
    btnCancel.addEventListener("click", () => {
      modal.style.display = "none";
      okCallback = null;
    });

    // OKボタン
    btnOk.addEventListener("click", () => {
      modal.style.display = "none";
      if (typeof okCallback === "function") okCallback();
      okCallback = null;
    });

    // モーダル背景クリックで閉じる
    modal.addEventListener("click", e => {
      if (e.target === modal) {
        modal.style.display = "none";
        okCallback = null;
      }
    });
  }

});