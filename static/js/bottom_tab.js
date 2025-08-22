document.addEventListener('DOMContentLoaded', () => {
  const submenu = document.getElementById('sub-menu');
  const toggle = document.getElementById('submenu-toggle');
  const back = document.getElementById('main-menu-toggle');

  // 長押し/右クリックでサブメニュー表示
  toggle.addEventListener('click', (e) => { e.preventDefault(); showSubMenu(); });
  toggle.addEventListener('contextmenu', (e) => { e.preventDefault(); showSubMenu(); });
  toggle.addEventListener('touchstart', handleLongPress);

  back.addEventListener('click', (e) => { e.preventDefault(); hideSubMenu(); });

  // 背景タップで閉じる
  document.addEventListener('click', (e) => {
    if(!submenu.contains(e.target) && !toggle.contains(e.target)) {
      hideSubMenu();
    }
  });

  function showSubMenu() {
    document.querySelector('.main-tabs').style.display = 'none';
    submenu.style.display = 'flex';
  }

  function hideSubMenu() {
    submenu.style.display = 'none';
    document.querySelector('.main-tabs').style.display = 'flex';
  }

  // 長押し検知
  let pressTimer;
  function handleLongPress(e) {
    e.preventDefault();
    pressTimer = setTimeout(showSubMenu, 600); // 0.6秒で長押し
  }
  toggle.addEventListener('touchend', () => clearTimeout(pressTimer));
});
