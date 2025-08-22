// iOSの100vh対策：--vh を1%の高さで更新
function applyVh(){
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
}
window.addEventListener('resize', applyVh);
window.addEventListener('orientationchange', applyVh);
applyVh();

// 現在URLに応じて下タブに .active を付与
document.addEventListener('DOMContentLoaded', () => {
  try{
    const path = location.pathname.replace(/\/+$/, '') || '/';
    document.querySelectorAll('.bottom-tab .tab-item').forEach(a => {
      const href = a.getAttribute('href')?.replace(/\/+$/, '') || '';
      if (href && (href === path || (href !== '/' && path.startsWith(href)))) {
        a.classList.add('active');
      }
    });
  }catch(e){ /* no-op */ }
});
