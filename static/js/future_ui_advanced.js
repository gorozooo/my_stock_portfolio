document.addEventListener("DOMContentLoaded", function(){
  const tabs = document.querySelectorAll('.tab-item');

  tabs.forEach(tab => {
    const subMenu = tab.querySelector('.sub-menu');

    // PC右クリック
    tab.addEventListener('contextmenu', e => {
      e.preventDefault();
      closeAllSubMenus();
      openSubMenu(subMenu, tab);
    });

    // スマホ長押し
    let timer;
    tab.addEventListener('touchstart', e => {
      timer = setTimeout(() => {
        closeAllSubMenus();
        openSubMenu(subMenu, tab);
      }, 500);
    });
    tab.addEventListener('touchend', e => clearTimeout(timer));
  });

  // 背景タップで閉じる
  document.addEventListener('click', e => {
    if(!e.target.closest('.tab-item')) closeAllSubMenus();
  });

  function openSubMenu(subMenu, tab){
    const rect = tab.getBoundingClientRect();
    subMenu.style.left = rect.left + "px";
    subMenu.classList.add('show');
  }

  function closeAllSubMenus(){
    document.querySelectorAll('.sub-menu').forEach(sm => sm.classList.remove('show'));
  }
});
