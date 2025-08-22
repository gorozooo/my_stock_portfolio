document.addEventListener('DOMContentLoaded', () => {
  const submenu = document.getElementById('sub-menu');
  const toggle = document.getElementById('submenu-toggle');
  const back = document.getElementById('main-menu-toggle');

  toggle.addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('.main-tabs').style.display = 'none';
    submenu.style.display = 'flex';
  });

  back.addEventListener('click', (e) => {
    e.preventDefault();
    submenu.style.display = 'none';
    document.querySelector('.main-tabs').style.display = 'flex';
  });
});
