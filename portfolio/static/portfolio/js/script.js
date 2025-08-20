<script>
document.querySelectorAll('.tab-item').forEach(tab => {
  let pressTimer;

  // モバイル：長押し
  tab.addEventListener('touchstart', e => {
    pressTimer = setTimeout(() => {
      tab.querySelector('.submenu')?.style.setProperty('display','block');
    }, 500); // 0.5秒で開く
  });

  tab.addEventListener('touchend', e => {
    clearTimeout(pressTimer);
  });

  // PC：右クリック
  tab.addEventListener('contextmenu', e => {
    e.preventDefault();
    tab.querySelector('.submenu')?.style.setProperty('display','block');
  });
});

// 背景タップで閉じる
document.addEventListener('click', e => {
  document.querySelectorAll('.submenu').forEach(menu => {
    if (!menu.contains(e.target) && !menu.parentElement.contains(e.target)) {
      menu.style.setProperty('display','none');
    }
  });
});
</script>
