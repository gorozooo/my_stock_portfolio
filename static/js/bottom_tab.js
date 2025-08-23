document.addEventListener('DOMContentLoaded', () => {
  const submenu = document.getElementById('sub-menu'); // サブメニュー本体
  const toggle = document.getElementById('submenu-toggle'); // サブメニュー開くボタン
  const back = document.getElementById('main-menu-toggle'); // メインメニュー戻るボタン

  // ---------- サブメニュー表示 ----------
  const showSubMenu = () => {
    const mainTabs = document.querySelector('.main-tabs');
    if (mainTabs) mainTabs.style.display = 'none';
    if (submenu) submenu.style.display = 'flex';
  };

  // ---------- サブメニュー非表示 ----------
  const hideSubMenu = () => {
    const mainTabs = document.querySelector('.main-tabs');
    if (submenu) submenu.style.display = 'none';
    if (mainTabs) mainTabs.style.display = 'flex';
  };

  // ---------- クリック／右クリック／長押し ----------
  if (toggle) {
    toggle.addEventListener('click', (e) => {
      e.preventDefault();
      showSubMenu();
    });
    toggle.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      showSubMenu();
    });

    // 長押し判定（タッチ端末用）
    let pressTimer;
    const handleLongPress = (e) => {
      e.preventDefault();
      pressTimer = setTimeout(showSubMenu, 600); // 600ms 長押しで開く
    };
    toggle.addEventListener('touchstart', handleLongPress);
    toggle.addEventListener('touchend', () => clearTimeout(pressTimer));
    toggle.addEventListener('touchcancel', () => clearTimeout(pressTimer));
  }

  // ---------- 戻るボタン ----------
  if (back) {
    back.addEventListener('click', (e) => {
      e.preventDefault();
      hideSubMenu();
    });
  }

  // ---------- 背景クリックで閉じる ----------
  document.addEventListener('click', (e) => {
    if (submenu && toggle) {
      if (!submenu.contains(e.target) && !toggle.contains(e.target)) {
        hideSubMenu();
      }
    }
  });

  // ---------- 背景タッチで閉じる（スマホ） ----------
  document.addEventListener('touchstart', (e) => {
    if (submenu && toggle) {
      if (!submenu.contains(e.target) && !toggle.contains(e.target)) {
        hideSubMenu();
      }
    }
  });
});
